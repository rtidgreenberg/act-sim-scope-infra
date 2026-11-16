#pragma once

#include "WanCodec.hpp"
#include "ActTypes.hpp"

#include <functional>
#include <memory>
#include <mutex>

namespace router {

class AsyncWaitSetDispatcher;
class ParticipantRegistry;
class QosResolver;

struct WanReceiveFilter {
    std::string expression;
    std::vector<std::string> parameters;
};

struct WanAdapterCallbacks {
    // May run on the strand or AWS worker: must not throw or reenter the factory.
    // Logical errors carry route generations; endpoint errors use physical names/epochs.
    std::function<void(const std::string&, const std::string&, uint64_t,
                       const std::string&)> error;
    std::function<void(const std::string&, const std::string&, uint64_t,
                       const std::string&)> warning;
};

struct WanBindingStatus {
    int32_t input_matched = 0;
    int32_t output_matched = 0;
    std::string warning;
};

struct WanSourceMetadataUpdate {
    std::string route;
    std::string topic;
    uint64_t generation;
    std::function<WanMetadata(const dds::core::xtypes::DynamicData&)> metadata;
};

struct WanStatusCallbacks {
    std::mutex mutex;
    std::function<void(const dds::core::xtypes::DynamicData&,
                       const dds::sub::SampleInfo&)> data;
    std::function<void(const dds::core::xtypes::DynamicData&)> health_missing;
};

struct WanConfigCallbacks {
    std::mutex mutex;
    std::function<void(const dds::core::xtypes::DynamicData&,
                       const dds::sub::SampleInfo&)> data;
};

class WanPresenceTransport {
public:
    virtual ~WanPresenceTransport() = default;
    virtual void bind_status(std::shared_ptr<WanStatusCallbacks> callbacks) = 0;
    virtual dds::pub::DataWriter<dds::core::xtypes::DynamicData> status_writer() const = 0;
    virtual dds::sub::DataReader<dds::core::xtypes::DynamicData> status_reader() const = 0;
    virtual void write_status(const dds::core::xtypes::DynamicData& frame) {
        status_writer().write(frame);
    }
    virtual void bind_config(std::shared_ptr<WanConfigCallbacks> callbacks) = 0;
    virtual void write_config(const dds::core::xtypes::DynamicData& frame) = 0;
    virtual std::vector<RouterWriterFilterObservation> writer_filter_observations() const = 0;
};

// Bind/unbind/shutdown run on the controller strand. Metadata providers run on
// AWS workers: read an immutable snapshot and never call back into this factory.
// Shared readers have one class-wide filter. Application lifecycle uses the same
// bound routes and native writer registry as valid data. Health lifecycle starts
// the presence countdown. Input instance retention is composed from external XML.
class WanAdapterFactory : public WanPresenceTransport {
public:
    using MetadataProvider = std::function<WanMetadata(const dds::core::xtypes::DynamicData&)>;

    WanAdapterFactory(ParticipantRegistry& participants, QosResolver& qos,
                      AsyncWaitSetDispatcher& dispatcher, dds::core::QosProvider types,
                      const std::string& wan_participant, const std::string& node,
                      const std::string& router, WanAdapterCallbacks callbacks = {},
                      bool separate_contacts = false);
    ~WanAdapterFactory();

    void bind_status(std::shared_ptr<WanStatusCallbacks> callbacks);
    bool status_bound() const;
    dds::pub::DataWriter<dds::core::xtypes::DynamicData> status_writer() const;
    dds::sub::DataReader<dds::core::xtypes::DynamicData> status_reader() const;
    void bind_config(std::shared_ptr<WanConfigCallbacks> callbacks);
    bool config_bound() const;
    dds::pub::DataWriter<dds::core::xtypes::DynamicData> config_writer() const;
    void write_config(const dds::core::xtypes::DynamicData& frame);
    void ignore_local_endpoints();
    dds::sub::DataReader<dds::core::xtypes::DynamicData> config_reader() const;
    void bind_source(const std::string& route, const std::string& topic, uint64_t generation,
                     const std::string& lan_participant, const std::string& reader_qos,
                     MetadataProvider metadata);
    void bind_destination(const std::string& route, const std::string& topic,
                          uint64_t generation, const std::string& lan_participant,
                          const std::string& writer_qos, const WanReceiveFilter& filter);
    void unbind(const std::string& route, const std::string& topic, uint64_t generation);
    void shutdown();
    void update_source_metadata(std::vector<WanSourceMetadataUpdate> updates);
    WanReceiveFilter receive_filter(const std::string& topic) const;
    void update_receive_filter(const std::string& topic, const WanReceiveFilter& filter);
    WanBindingStatus binding_status(const std::string& route, const std::string& topic) const;
    std::vector<RouterWriterFilterObservation> writer_filter_observations() const;

    uint64_t forwarded(const std::string& route, const std::string& topic) const;
    size_t wan_writers() const;
    size_t wan_readers() const;
    size_t source_readers() const;
    size_t destination_writers() const;

private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

} // namespace router
