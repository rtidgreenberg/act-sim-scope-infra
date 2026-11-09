#include "WanAdapterFactory.hpp"

#include "AsyncWaitSetDispatcher.hpp"
#include "Log.hpp"
#include "ParticipantRegistry.hpp"
#include "QosResolver.hpp"
#include "WanInterests.hpp"

#include <algorithm>
#include <atomic>
#include <iterator>
#include <map>
#include <mutex>
#include <stdexcept>
#include <utility>

namespace router {
namespace {

using Data = dds::core::xtypes::DynamicData;
using Reader = dds::sub::DataReader<Data>;
using Writer = dds::pub::DataWriter<Data>;
using Topic = dds::topic::Topic<Data>;
using Cft = dds::topic::ContentFilteredTopic<Data>;
using Counter = std::shared_ptr<std::atomic<uint64_t>>;
using Error = std::function<void(const std::string&)>;

std::string pair_key(const std::string& first, const std::string& second) {
    return std::to_string(first.size()) + ":" + first + second;
}

void require(bool condition, const std::string& message) {
    if (!condition) {
        throw std::runtime_error("WAN adapters: " + message);
    }
}

Topic topic(dds::domain::DomainParticipant participant, const std::string& name,
            const dds::core::xtypes::DynamicType& type) {
    auto found = dds::topic::find<Topic>(participant, name);
    if (found != dds::core::null) {
        require(found.type_name() == type.name(), "existing topic/type name mismatch: " + name);
        return found;
    }
    return Topic(participant, name, type);
}

class InputRuntime : public RouteTopicRuntimeBase {
public:
    InputRuntime(Reader reader, dds::sub::Subscriber subscriber, Cft cft,
                 std::function<bool(const Data&, const dds::sub::SampleInfo&)> receive,
                 Error error, bool wan,
                 const std::string& physical_name, Error warning = {},
                 std::function<bool(const dds::sub::SampleInfo&)> lifecycle = {})
        : reader_(reader), subscriber_(subscriber), cft_(cft),
          condition_(reader_, dds::sub::status::DataState::any(), [this] { pump(); }),
          status_(reader_), receive_(std::move(receive)), error_(std::move(error)),
          wan_(wan), physical_name_(physical_name), warning_(std::move(warning)),
          lifecycle_(std::move(lifecycle)) {
        using dds::core::status::StatusMask;
        status_.enabled_statuses(StatusMask::subscription_matched()
                                | StatusMask::requested_incompatible_qos());
        status_->handler([this] {
            using dds::core::status::StatusMask;
            const auto changes = reader_.status_changes();
            if ((changes & StatusMask::subscription_matched()).any()) {
                reader_.subscription_matched_status();
            }
            if ((changes & StatusMask::requested_incompatible_qos()).any()) {
                warn("reader QoS: " + qos_policy_name(
                    reader_.requested_incompatible_qos_status().last_policy_id()));
            }
        });
    }
    std::vector<dds::core::cond::Condition> conditions() const override {
        return {condition_, status_};
    }
    void close() override {
        condition_.close();
        reader_.set_listener(nullptr);
        reader_.close();
        if (cft_ != dds::core::null) {
            cft_.close();
        }
        subscriber_.close();
    }
    uint64_t forwarded() const override { return count_.load(); }
    bool has_wan_leg() const override { return wan_; }
    void collect_wan_stats(LinkStatsSink& sink) override {
        if (wan_) {
            poll_reader_wan_stats(reader_, previous_, sink, "@wan/reader", physical_name_);
        }
    }
    bool set_partitions(const std::vector<std::string>&,
                        const std::vector<std::string>&) override {
        error_("shared adapter partitions require class-wide configuration");
        return false;
    }
private:
    void pump() {
        try {
            const auto samples = reader_.select().condition(condition_).take();
            for (const auto& sample : samples) {
                if (!sample.info().valid()) {
                    if (!lifecycle_ || !lifecycle_(sample.info())) {
                        warn("instance lifecycle forwarding is not enabled in this pilot");
                    }
                    continue;
                }
                try {
                    if (receive_(sample.data(), sample.info())) {
                        count_.fetch_add(1);
                    }
                } catch (const std::exception& e) {
                    error_(e.what());
                }
            }
        } catch (const std::exception& e) {
            error_(e.what());
        }
    }
    void warn(const std::string& message) {
        (warning_ ? warning_ : error_)(message);
    }
    Reader reader_;
    dds::sub::Subscriber subscriber_;
    Cft cft_;
    dds::sub::cond::ReadCondition condition_;
    dds::core::cond::StatusCondition status_;
    std::function<bool(const Data&, const dds::sub::SampleInfo&)> receive_;
    Error error_;
    bool wan_;
    std::string physical_name_;
    std::atomic<uint64_t> count_{0};
    std::map<std::string, ReaderTotals> previous_;
    Error warning_;
    std::function<bool(const dds::sub::SampleInfo&)> lifecycle_;
};

struct Output {
    Writer writer{dds::core::null};
    dds::pub::Publisher publisher{dds::core::null};
    size_t users = 0;
    std::string runtime_name;
    std::string topic_name;
    std::string qos_alias;
};

class OutputRuntime : public RouteTopicRuntimeBase {
public:
    OutputRuntime(std::shared_ptr<Output> output, Error error, bool wan, Error warning = {})
        : output_(std::move(output)), status_(output_->writer),
          error_(std::move(error)), wan_(wan), warning_(std::move(warning)) {
        using dds::core::status::StatusMask;
        status_.enabled_statuses(StatusMask::publication_matched()
                                | StatusMask::offered_incompatible_qos());
        status_->handler([this] {
            using dds::core::status::StatusMask;
            const auto changes = output_->writer.status_changes();
            if ((changes & StatusMask::publication_matched()).any()) {
                output_->writer.publication_matched_status();
            }
            if ((changes & StatusMask::offered_incompatible_qos()).any()) {
                (warning_ ? warning_ : error_)("writer QoS: " + qos_policy_name(
                    output_->writer.offered_incompatible_qos_status().last_policy_id()));
            }
        });
    }
    std::vector<dds::core::cond::Condition> conditions() const override { return {status_}; }
    void close() override {
        output_->writer.close();
        output_->publisher.close();
    }
    uint64_t forwarded() const override { return 0; }
    bool has_wan_leg() const override { return wan_; }
    void collect_wan_stats(LinkStatsSink& sink) override {
        if (wan_) {
            poll_writer_wan_stats(output_->writer, previous_, sink,
                                  "@wan/writer", output_->topic_name);
        }
    }
    bool set_partitions(const std::vector<std::string>&,
                        const std::vector<std::string>&) override {
        error_("shared adapter partitions require class-wide configuration");
        return false;
    }
private:
    std::shared_ptr<Output> output_;
    dds::core::cond::StatusCondition status_;
    Error error_;
    bool wan_;
    std::map<std::string, WriterTotals> previous_;
    Error warning_;
};

struct Contribution {
    uint64_t generation;
    std::string route;
    WanAdapterFactory::MetadataProvider metadata;
    Counter count;
};

struct Source {
    std::mutex mutex;
    std::map<std::string, Contribution> routes;
    std::string class_key;
    std::string qos_alias;
    std::string runtime_name;
    std::string topic_name;
    Reader reader{dds::core::null};
};

struct Target {
    std::shared_ptr<Output> output;
    std::map<std::string, Contribution> routes;
};

struct Receiver {
    std::mutex mutex;
    std::map<std::string, Target> targets;
    WanReceiveFilter filter;
    std::string runtime_name;
    std::string family;
    Reader reader{dds::core::null};
    Cft cft{dds::core::null};
    std::shared_ptr<WanStatusCallbacks> observer;
};

class StatusDeadlineListener : public dds::sub::NoOpDataReaderListener<Data> {
public:
    StatusDeadlineListener(dds::core::QosProvider types, std::weak_ptr<Receiver> state)
        : types_(types), codec_(types), state_(std::move(state)) {}

    void on_requested_deadline_missed(Reader& reader,
            const dds::core::status::RequestedDeadlineMissedStatus& status) override {
        try {
            auto state = state_.lock();
            if (!state || !state->observer) return;
            Data key(types_->type("WanStatus"));
            reader.key_value(key, status.last_instance_handle());
            if (codec_.unpack_key(key).topic == "RouterHealth") {
                state->observer->health_missing(key);
            }
        } catch (const std::exception& error) {
            Log::warn("wan_status_deadline_failed", {{"error", error.what()}});
        }
    }

private:
    dds::core::QosProvider types_;
    WanCodec codec_;
    std::weak_ptr<Receiver> state_;
};

struct Binding {
    uint64_t generation;
    bool source;
    std::string owner_key;
    std::string output_key;
    std::string class_key;
    std::string topic_name;
    std::string route;
    Counter count;
};

WanMetadata merge(const WanMetadata& a, const WanMetadata& b) {
    require(a.source_id == b.source_id
                && a.source_role == b.source_role && a.run_id == b.run_id
                && a.session_id == b.session_id && a.audit_sequence == b.audit_sequence
                && a.sent_at_ns == b.sent_at_ns
                && a.membership_revision == b.membership_revision,
            "source bindings disagree on immutable sample metadata");
    require(a.destination.empty() || b.destination.empty() || a.destination == b.destination,
            "source bindings require different addressed destinations");
    auto result = a;
    result.control = a.control || b.control;
    result.global = a.global || b.global;
    if (result.destination.empty()) {
        result.destination = b.destination;
    }
    for (const auto& team : b.team_ids) {
        if (std::find(result.team_ids.begin(), result.team_ids.end(), team) == result.team_ids.end()) {
            result.team_ids.push_back(team);
        }
    }
    require(result.team_ids.size() <= 16, "combined team audience exceeds 16 entries");
    return result;
}

} // namespace

struct WanAdapterFactory::Impl {
    ParticipantRegistry& participants;
    QosResolver& qos;
    AsyncWaitSetDispatcher& dispatcher;
    dds::core::QosProvider types;
    WanCodec codec;
    std::string wan;
    std::string node;
    std::string router;
    WanAdapterCallbacks callbacks;
    uint64_t epoch = 0;
    std::map<std::string, Binding> bindings;
    std::map<std::string, std::shared_ptr<Source>> sources;
    std::map<std::string, std::shared_ptr<Receiver>> receivers;
    std::map<std::string, std::shared_ptr<Output>> writers;
    std::map<std::string, std::shared_ptr<Output>> local_outputs;
    std::map<std::string, std::string> owned_runtimes;
    std::shared_ptr<WanStatusCallbacks> status_observer;

    Impl(ParticipantRegistry& p, QosResolver& q, AsyncWaitSetDispatcher& d,
         dds::core::QosProvider t, const std::string& w, const std::string& n,
         const std::string& r, WanAdapterCallbacks c)
        : participants(p), qos(q), dispatcher(d), types(t), codec(t),
          wan(w), node(n), router(r), callbacks(std::move(c)) {
        require(participants.on_wan(wan), "selected participant is not WAN");
        require(!node.empty() && !router.empty(), "empty router identity");
    }

    void error(const std::string& route, const std::string& topic_name,
               uint64_t generation, const std::string& message) {
        Log::error("wan_adapter_error", {{"route", route}, {"topic", topic_name},
                   {"generation", std::to_string(generation)}, {"error", message}});
        if (callbacks.error) {
            callbacks.error(route, topic_name, generation, message);
        }

    }

    void warning(const std::string& route, const std::string& topic_name,
                 uint64_t generation, const std::string& message) {
        if (!callbacks.warning) {
            error(route, topic_name, generation, message);
            return;
        }
        Log::warn("wan_adapter_warning", {{"route", route}, {"topic", topic_name},
                  {"generation", std::to_string(generation)}, {"warning", message}});
        callbacks.warning(route, topic_name, generation, message);
    }

    void attach(const std::string& name, const std::string& topic_name,
                std::unique_ptr<RouteTopicRuntimeBase> runtime) {
        owned_runtimes.emplace(name, topic_name);
        try {
            dispatcher.attach(name, topic_name, std::move(runtime));
        } catch (const std::exception&) {
            detach(name, topic_name);
            throw;
        }
    }

    void detach(const std::string& name, const std::string& topic_name) {
        dispatcher.detach_and_close(name, topic_name);
        owned_runtimes.erase(name);
    }

    std::shared_ptr<Output> make_output(const std::string& participant,
            const std::string& topic_name, const std::string& type,
            const std::string& qos_alias, bool is_wan) {
        auto output = std::make_shared<Output>();
        output->runtime_name = (is_wan ? "@wan/writer/" : "@wan/lan_writer/")
                                + std::to_string(++epoch);
        output->topic_name = topic_name;
        output->qos_alias = qos_alias;
        auto dp = participants.get(participant);
        auto writer_qos = qos.writer_qos(qos_alias);
        auto description = topic(dp, topic_name, types->type(type));
        output->publisher = dds::pub::Publisher(dp);
        output->writer = Writer(output->publisher, description, writer_qos);
        if (!is_wan || dp->enabled()) {
            dds::pub::ignore(dp, output->writer.instance_handle());
        }
        const auto generation = epoch;
        const auto name = output->runtime_name;
        Error report = [this, name, topic_name, generation](const std::string& message) {
            error(name, topic_name, generation, message);
        };
        Error warn = [this, name, topic_name, generation](const std::string& message) {
            warning(name, topic_name, generation, message);
        };
        attach(name, topic_name, std::unique_ptr<RouteTopicRuntimeBase>(
            new OutputRuntime(output, report, is_wan, warn)));
        return output;
    }

    void release_output(std::map<std::string, std::shared_ptr<Output>>& outputs,
                        const std::string& key) {
        const auto it = outputs.find(key);
        require(it != outputs.end() && it->second->users, "missing output lease");
        if (it->second->users == 1) {
            detach(it->second->runtime_name, it->second->topic_name);
            outputs.erase(it);
        } else {
            --it->second->users;
        }
    }

    void bind_source(const Binding& binding, const std::string& participant,
                     const std::string& alias, MetadataProvider provider) {
        const auto id = pair_key(binding.route, binding.topic_name);
        require(bool(provider), "source metadata provider is required");
        require(!participants.on_wan(participant), "source input must be LAN");
        auto existing = sources.find(binding.owner_key);
        if (existing != sources.end()) {
            require(existing->second->qos_alias == alias, "shared source reader QoS conflict");
            std::lock_guard<std::mutex> lock(existing->second->mutex);
            existing->second->routes.emplace(id, Contribution{
                binding.generation, binding.route, provider, binding.count});
            return;
        }
        const auto& mapping = wan_mapping(binding.topic_name);
        auto reader_qos = qos.reader_qos(alias);
        auto state = std::make_shared<Source>();
        state->qos_alias = alias;
        state->class_key = binding.class_key;
        state->topic_name = binding.topic_name;
        state->runtime_name = "@wan/source/" + std::to_string(++epoch);
        const auto source_epoch = epoch;
        state->routes.emplace(id, Contribution{
            binding.generation, binding.route, provider, binding.count});
        auto found_writer = writers.find(binding.class_key);
        if (found_writer == writers.end()) {
            found_writer = writers.emplace(binding.class_key, make_output(
                wan, mapping.family, std::string("Wan") + mapping.family, mapping.qos_profile, true)).first;
        }
        const auto output = found_writer->second;
        ++output->users;
        try {
            auto dp = participants.get(participant);
            auto description = topic(dp, binding.topic_name, types->type(mapping.type));
            dds::sub::Subscriber subscriber(dp);
            Reader reader(subscriber, description, reader_qos);
            state->reader = reader;
            auto receive = [this, state, output](const Data& data, const dds::sub::SampleInfo&) {
                std::lock_guard<std::mutex> lock(state->mutex);
                if (state->routes.empty()) {
                    return false;
                }
                auto first = state->routes.begin();
                auto metadata = first->second.metadata(data);
                for (auto it = std::next(first); it != state->routes.end(); ++it) {
                    metadata = merge(metadata, it->second.metadata(data));
                }
                require(metadata.source_id == node,
                        "metadata origin differs from router identity");
                output->writer.write(codec.pack(state->topic_name, data, metadata));
                for (auto& route : state->routes) {
                    route.second.count->fetch_add(1);
                }
                return true;
            };
            Error report = [this, state, source_epoch](const std::string& message) {
                std::lock_guard<std::mutex> lock(state->mutex);
                if (state->routes.empty()) {
                    error(state->runtime_name, state->topic_name, source_epoch, message);
                    return;
                }
                for (const auto& route : state->routes) {
                    error(route.second.route, state->topic_name, route.second.generation, message);
                }
            };
            Error warn = [this, state, source_epoch](const std::string& message) {
                warning(state->runtime_name, state->topic_name, source_epoch, message);
            };
            sources.emplace(binding.owner_key, state);
            attach(state->runtime_name, state->topic_name,
                std::unique_ptr<RouteTopicRuntimeBase>(new InputRuntime(
                    reader, subscriber, dds::core::null, receive, report, false, state->topic_name, warn)));
        } catch (const std::exception&) {
            detach(state->runtime_name, state->topic_name);
            sources.erase(binding.owner_key);
            release_output(writers, binding.class_key);
            throw;
        }
    }

    WanReceiveFilter effective_filter(const WanReceiveFilter& filter,
                                      const std::string& family) const {
        auto result = filter;
        if (status_observer && family == "Status") {
            result.expression = "(" + result.expression + ") OR (source_id <> %0 AND payload._d = "
                    + std::to_string(wan_mapping("RouterHealth").discriminator) + ")";
        }
        size_t property_size = result.expression.size();
        for (const auto& parameter : result.parameters) property_size += parameter.size() + 1;
        require(property_size < 16384, "effective WAN filter exceeds the 16384-byte property budget");
        return result;
    }

    std::shared_ptr<Receiver> make_receiver(const std::string& family,
            const std::string& qos_alias, const WanReceiveFilter& filter) {
        auto state = std::make_shared<Receiver>();
        state->filter = filter;
        state->family = family;
        state->observer = family == "Status" ? status_observer : nullptr;
        state->runtime_name = "@wan/reader/" + std::to_string(++epoch);
        auto dp = participants.get(wan);
        auto description = topic(dp, family, types->type("Wan" + family));
        dds::sub::Subscriber subscriber(dp);
        const auto selected = effective_filter(filter, family);
        Cft cft(description, state->runtime_name + "_cft",
                dds::topic::Filter(selected.expression, selected.parameters));
        Reader reader(subscriber, cft, qos.reader_qos(qos_alias));
        state->reader = reader;
        state->cft = cft;
        if (state->observer) {
            reader.set_listener(std::make_shared<StatusDeadlineListener>(types, state),
                                dds::core::status::StatusMask::requested_deadline_missed());
        }
        auto receive = [this, state](const Data& frame, const dds::sub::SampleInfo& info) {
            if (frame.value<std::string>("source_id") == node) return false;
            const auto decoded = codec.unpack(frame);
            if (state->observer) {
                std::lock_guard<std::mutex> callback_lock(state->observer->mutex);
                if (state->observer->data) state->observer->data(frame, info);
            }
            std::lock_guard<std::mutex> lock(state->mutex);
            bool written = false;
            for (auto& item : state->targets) {
                auto& target = item.second;
                if (target.output->topic_name != decoded.topic) continue;
                try {
                    target.output->writer.write(decoded.data);
                    written = true;
                    for (auto& route : target.routes) route.second.count->fetch_add(1);
                } catch (const std::exception& e) {
                    for (const auto& route : target.routes) {
                        error(route.second.route, decoded.topic, route.second.generation, e.what());
                    }
                }
            }
            return written;
        };
        const auto generation = epoch;
        Error report = [this, state, generation](const std::string& message) {
            error(state->runtime_name, state->family, generation, message);
        };
        Error warn = [this, state, generation](const std::string& message) {
            warning(state->runtime_name, state->family, generation, message);
        };
        auto lifecycle = [this, state](const dds::sub::SampleInfo& info) {
            if (!state->observer) return false;
            Data key(types->type("WanStatus"));
            state->reader.key_value(key, info.instance_handle());
            if (codec.unpack_key(key).topic != "RouterHealth") return false;
            if (key.value<std::string>("source_id") != node) {
                state->observer->health_missing(key);
            }
            return true;
        };
        try {
            attach(state->runtime_name, state->family,
                std::unique_ptr<RouteTopicRuntimeBase>(new InputRuntime(
                    reader, subscriber, cft, receive, report, true, state->family, warn, lifecycle)));
        } catch (const std::exception&) {
            reader.set_listener(nullptr);
            throw;
        }
        return state;
    }

    void bind_destination(const Binding& binding, const std::string& participant,
                          const std::string& alias, const WanReceiveFilter& filter) {
        require(!participants.on_wan(participant), "destination output must be LAN");
        require(!filter.expression.empty(), "class-wide receive CFT is required");
        auto existing = receivers.find(binding.class_key);
        if (existing != receivers.end()) {
            if (existing->second->observer && existing->second->targets.empty()) {
                const auto selected = effective_filter(filter, wan_mapping(binding.topic_name).family);
                existing->second->cft->filter(dds::topic::Filter(
                        selected.expression, selected.parameters));
                existing->second->filter = filter;
            }
            require(existing->second->filter.expression == filter.expression
                        && existing->second->filter.parameters == filter.parameters,
                    "shared receive filter conflict");
        }
        const auto& mapping = wan_mapping(binding.topic_name);
        auto output_it = local_outputs.find(binding.output_key);
        if (output_it != local_outputs.end()) {
            require(output_it->second->qos_alias == alias, "shared LAN writer QoS conflict");
        } else {
            output_it = local_outputs.emplace(binding.output_key,
                make_output(participant, binding.topic_name, mapping.type, alias, false)).first;
        }
        auto output = output_it->second;
        ++output->users;
        std::shared_ptr<Receiver> created;
        try {
            if (existing == receivers.end()) {
                auto state = make_receiver(mapping.family, mapping.qos_profile, filter);
                created = state;
                existing = receivers.emplace(binding.class_key, state).first;
            }
            auto state = existing->second;
            std::lock_guard<std::mutex> lock(state->mutex);
            auto& target = state->targets[binding.output_key];
            target.output = output;
            target.routes.emplace(pair_key(binding.route, binding.topic_name),
                Contribution{binding.generation, binding.route, {}, binding.count});
        } catch (const std::exception&) {
            if (created) {
                detach(created->runtime_name, created->family);
                receivers.erase(binding.class_key);
            }
            release_output(local_outputs, binding.output_key);
            throw;
        }
    }

    void unbind(const std::string& id, uint64_t generation) {
        const auto it = bindings.find(id);
        require(it != bindings.end(), "unknown binding");
        const auto binding = it->second;
        require(binding.generation == generation, "stale binding generation");
        if (binding.source) {
            auto source = sources.at(binding.owner_key);
            bool empty;
            {
                std::lock_guard<std::mutex> lock(source->mutex);
                source->routes.erase(id);
                empty = source->routes.empty();
            }
            if (empty) {
                detach(source->runtime_name, source->topic_name);
                sources.erase(binding.owner_key);
                release_output(writers, binding.class_key);
            }
        } else {
            auto receiver = receivers.at(binding.class_key);
            bool empty;
            {
                std::lock_guard<std::mutex> lock(receiver->mutex);
                auto& target = receiver->targets.at(binding.output_key);
                target.routes.erase(id);
                if (target.routes.empty()) {
                    receiver->targets.erase(binding.output_key);
                }
                empty = receiver->targets.empty() && !receiver->observer;
            }
            if (empty) {
                detach(receiver->runtime_name, receiver->family);
                receivers.erase(binding.class_key);
            }
            release_output(local_outputs, binding.output_key);
        }
        bindings.erase(it);
    }
};

WanAdapterFactory::WanAdapterFactory(ParticipantRegistry& participants, QosResolver& qos,
        AsyncWaitSetDispatcher& dispatcher, dds::core::QosProvider types,
        const std::string& wan, const std::string& node, const std::string& router,
        WanAdapterCallbacks callbacks)
    : impl_(new Impl(participants, qos, dispatcher, types, wan, node, router, std::move(callbacks))) {}

WanAdapterFactory::~WanAdapterFactory() { shutdown(); }

void WanAdapterFactory::bind_status(std::shared_ptr<WanStatusCallbacks> callbacks) {
    require(callbacks && callbacks->data && callbacks->health_missing, "Status callbacks are required");
    require(!impl_->status_observer && impl_->bindings.empty(),
            "Status observer must be bound before application routes");
    impl_->status_observer = std::move(callbacks);
    const auto& mapping = wan_mapping("RouterHealth");
    const auto key = pair_key(mapping.family, mapping.qos_profile);
    try {
        auto output = impl_->make_output(impl_->wan, "Status", "WanStatus", mapping.qos_profile, true);
        output->users = 1;
        impl_->writers.emplace(key, std::move(output));
        impl_->receivers.emplace(key, impl_->make_receiver("Status", mapping.qos_profile,
                wan_receive_filter("Status", impl_->node, {})));
    } catch (const std::exception&) {
        shutdown();
        throw;
    }
}

bool WanAdapterFactory::status_bound() const { return bool(impl_->status_observer); }

Writer WanAdapterFactory::status_writer() const {
    const auto& mapping = wan_mapping("RouterHealth");
    return impl_->writers.at(pair_key(mapping.family, mapping.qos_profile))->writer;
}

Reader WanAdapterFactory::status_reader() const {
    const auto& mapping = wan_mapping("RouterHealth");
    return impl_->receivers.at(pair_key(mapping.family, mapping.qos_profile))->reader;
}

void WanAdapterFactory::update_source_metadata(std::vector<WanSourceMetadataUpdate> updates) {
    std::map<std::string, std::vector<WanSourceMetadataUpdate>> grouped;
    for (auto& update : updates) {
        const auto& binding = impl_->bindings.at(pair_key(update.route, update.topic));
        require(binding.source && binding.generation == update.generation && bool(update.metadata),
                "invalid source metadata update");
        grouped[binding.owner_key].push_back(std::move(update));
    }
    for (auto& group : grouped) {
        auto source = impl_->sources.at(group.first);
        std::lock_guard<std::mutex> lock(source->mutex);
        for (auto& update : group.second) {
            source->routes.at(pair_key(update.route, update.topic)).metadata = std::move(update.metadata);
        }
    }
}

WanReceiveFilter WanAdapterFactory::receive_filter(const std::string& topic_name) const {
    const auto& mapping = wan_mapping(topic_name);
    return impl_->receivers.at(pair_key(mapping.family, mapping.qos_profile))->filter;
}

void WanAdapterFactory::update_receive_filter(const std::string& topic_name,
                                            const WanReceiveFilter& filter) {
    const auto& mapping = wan_mapping(topic_name);
    auto receiver = impl_->receivers.at(pair_key(mapping.family, mapping.qos_profile));
    auto next = filter;
    const auto selected = impl_->effective_filter(next, receiver->family);
    receiver->cft->filter(dds::topic::Filter(selected.expression, selected.parameters));
    receiver->filter = std::move(next);
}

WanBindingStatus WanAdapterFactory::binding_status(const std::string& route,
                                                 const std::string& topic_name) const {
    const auto& binding = impl_->bindings.at(pair_key(route, topic_name));
    const auto reader = binding.source ? impl_->sources.at(binding.owner_key)->reader
                                      : impl_->receivers.at(binding.class_key)->reader;
    auto writer = binding.source ? impl_->writers.at(binding.class_key)->writer
                                      : impl_->local_outputs.at(binding.output_key)->writer;
    WanBindingStatus status;
    status.input_matched = reader.subscription_matched_status().current_count();
    status.output_matched = writer.publication_matched_status().current_count();
    const auto requested = reader.requested_incompatible_qos_status();
    const auto offered = writer.offered_incompatible_qos_status();
    if (requested.total_count()) {
        status.warning = "reader QoS: " + qos_policy_name(requested.last_policy_id());
    }
    if (offered.total_count()) {
        status.warning += (status.warning.empty() ? "" : "; ") + std::string("writer QoS: ")
                          + qos_policy_name(offered.last_policy_id());
    }
    return status;
}

void WanAdapterFactory::bind_source(const std::string& route, const std::string& topic_name,
        uint64_t generation, const std::string& lan, const std::string& alias,
        MetadataProvider metadata) {
    const auto id = pair_key(route, topic_name);
    require(generation && !route.empty(), "binding requires route and nonzero generation");
    require(!impl_->bindings.count(id), "binding already exists");
    const auto& mapping = wan_mapping(topic_name);
    Binding binding{generation, true, pair_key(lan, topic_name), {},
                    pair_key(mapping.family, mapping.qos_profile), topic_name, route,
                    std::make_shared<std::atomic<uint64_t>>(0)};
    try {
        impl_->bind_source(binding, lan, alias, std::move(metadata));
        impl_->bindings.emplace(id, binding);
    } catch (const std::exception& e) {
        impl_->error(route, topic_name, generation, e.what());
        throw;
    }
}

void WanAdapterFactory::bind_destination(const std::string& route, const std::string& topic_name,
        uint64_t generation, const std::string& lan, const std::string& alias,
        const WanReceiveFilter& filter) {
    const auto id = pair_key(route, topic_name);
    require(generation && !route.empty(), "binding requires route and nonzero generation");
    require(!impl_->bindings.count(id), "binding already exists");
    const auto& mapping = wan_mapping(topic_name);
    Binding binding{generation, false, {}, pair_key(lan, topic_name),
                    pair_key(mapping.family, mapping.qos_profile), topic_name, route,
                    std::make_shared<std::atomic<uint64_t>>(0)};
    try {
        impl_->bind_destination(binding, lan, alias, filter);
        impl_->bindings.emplace(id, binding);
    } catch (const std::exception& e) {
        impl_->error(route, topic_name, generation, e.what());
        throw;
    }
}

void WanAdapterFactory::unbind(const std::string& route, const std::string& topic_name,
                               uint64_t generation) {
    impl_->unbind(pair_key(route, topic_name), generation);
}

void WanAdapterFactory::shutdown() {
    while (!impl_->bindings.empty()) {
        const auto it = impl_->bindings.begin();
        impl_->unbind(it->first, it->second.generation);
    }
    // Failed construction may leave an owned runtime if a detach barrier failed.
    // Stop all input handlers before reclaiming any remaining output endpoint.
    for (auto it = impl_->owned_runtimes.begin(); it != impl_->owned_runtimes.end();) {
        const auto name = it->first;
        const auto topic_name = it->second;
        ++it;
        if (name.find("@wan/source/") == 0 || name.find("@wan/reader/") == 0) {
            impl_->detach(name, topic_name);
        }
    }
    while (!impl_->owned_runtimes.empty()) {
        const auto item = *impl_->owned_runtimes.begin();
        impl_->detach(item.first, item.second);
    }
    impl_->sources.clear();
    impl_->receivers.clear();
    impl_->writers.clear();
    impl_->local_outputs.clear();
    impl_->status_observer.reset();
}

uint64_t WanAdapterFactory::forwarded(const std::string& route, const std::string& topic_name) const {
    const auto found = impl_->bindings.find(pair_key(route, topic_name));
    require(found != impl_->bindings.end(), "count requested for unknown binding");
    return found->second.count->load();
}

size_t WanAdapterFactory::wan_writers() const { return impl_->writers.size(); }
size_t WanAdapterFactory::wan_readers() const { return impl_->receivers.size(); }
size_t WanAdapterFactory::source_readers() const { return impl_->sources.size(); }
size_t WanAdapterFactory::destination_writers() const { return impl_->local_outputs.size(); }

} // namespace router
