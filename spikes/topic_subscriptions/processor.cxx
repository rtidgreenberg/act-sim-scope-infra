#include <rti/routing/processor/ProcessorPlugin.hpp>

#include <cstdio>
#include <fstream>
#include <iostream>
#include <map>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

using Data = dds::core::xtypes::DynamicData;
using Route = rti::routing::processor::Route;
using Properties = rti::routing::PropertySet;
using rti::routing::PropertySet;

class SubscriptionProcessor : public rti::routing::processor::NoOpProcessor {
public:
    explicit SubscriptionProcessor(const Properties& properties)
        : path_(properties.at("fixture.control_path")) {}

    void on_data_available(Route& route) override { forward(route); }

    void on_periodic_action(Route& route) override {
        std::ifstream input(path_ + ".request");
        std::string identity, action, parameter;
        if (!std::getline(input, identity) || identity == previous_) return;
        if (!std::getline(input, action)) {
            throw std::runtime_error("incomplete fixture control request");
        }
        std::vector<std::string> parameters;
        while (std::getline(input, parameter)) parameters.push_back(parameter);
        input.close();
        if (std::remove((path_ + ".request").c_str()) != 0) {
            throw std::runtime_error("failed to consume fixture control request");
        }
        previous_ = identity;
        std::string error, expression;
        std::vector<std::string> actual;
        try {
            auto description = route.input<Data>("input").dds_data_reader().topic_description();
            auto topic = dds::core::polymorphic_cast<dds::topic::ContentFilteredTopic<Data>>(description);
            if (action == "set") {
                if (parameters.size() != 6) throw std::runtime_error("expected six parameters");
                topic->filter(dds::topic::Filter(topic.filter_expression(), parameters));
            } else if (action != "get") {
                throw std::runtime_error("invalid fixture control action");
            }
            expression = topic.filter_expression();
            actual = topic.filter_parameters();
        } catch (const std::exception& failure) {
            error = failure.what();
            std::cerr << "SPIKE_CONTROL_ERROR " << error << std::endl;
        }
        std::ofstream output(path_ + ".new");
        output << identity << '\n' << (error.empty() ? "0" : "1") << '\n'
               << error << '\n' << expression << '\n';
        for (const auto& value : actual) output << value << '\n';
        output.close();
        if (!output || std::rename((path_ + ".new").c_str(), (path_ + ".reply").c_str()) != 0) {
            throw std::runtime_error("failed to publish native fixture readback");
        }
        forward(route);
    }

private:
    void forward(Route& route) {
        auto input = route.input<Data>("input");
        const auto samples = input.select().max_samples(32).take();
        auto writer = route.output<Data>("output").dds_data_writer();
        std::map<std::string, dds::sub::SampleInfo> states;
        for (const auto& sample : samples) {
            std::ostringstream handle;
            handle << sample.info().instance_handle();
            states[handle.str()] = sample.info();
            if (sample.info().valid()) writer.write(sample.data());
        }
        for (const auto& entry : states) {
            const auto& info = entry.second;
            using State = dds::sub::status::InstanceState;
            if (info.state().instance_state() == State::alive()) continue;
            Data key = input.create_data();
            input.dds_data_reader().key_value(key, info.instance_handle());
            const auto handle = writer.lookup_instance(key);
            if (handle == dds::core::null) {
                std::cerr << "SPIKE_LIFECYCLE_UNMATERIALIZED " << entry.first << std::endl;
                continue;
            }
            rti::pub::WriteParams parameters;
            parameters.handle(handle);
            if (info.state().instance_state() == State::not_alive_disposed()) {
                writer->dispose_instance(parameters);
            } else if (info.state().instance_state() == State::not_alive_no_writers()) {
                writer->unregister_instance(parameters);
            } else {
                throw std::runtime_error("unsupported lifecycle state");
            }
        }
    }
    std::string path_, previous_;
};

class SubscriptionPlugin : public rti::routing::processor::ProcessorPlugin {
public:
    explicit SubscriptionPlugin(const Properties&) {}
    rti::routing::processor::Processor* create_processor(Route&, const Properties& properties) override {
        return new SubscriptionProcessor(properties);
    }
    void delete_processor(Route&, rti::routing::processor::Processor* processor) override {
        delete processor;
    }
};

RTI_PROCESSOR_PLUGIN_CREATE_FUNCTION_DECL(SubscriptionPlugin)
RTI_PROCESSOR_PLUGIN_CREATE_FUNCTION_DEF(SubscriptionPlugin)
