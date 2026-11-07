#include <dds/dds.hpp>
#include <dds/domain/discovery.hpp>
#include <rti/routing/processor/Processor.hpp>
#include <rti/routing/processor/Route.hpp>

#include <string>

namespace {

using dds::core::xtypes::DynamicData;
using rti::routing::processor::Route;

void check_peer_api(dds::domain::DomainParticipant participant)
{
    const std::string peer = "[6]@udpv4://127.0.0.1";
    participant->add_peer(peer);
    const auto handles = dds::domain::discovered_participants(participant);
    for (const auto& handle : handles) {
        const auto data =
                dds::domain::discovered_participant_data(participant, handle);
        (void) data;
    }
    participant->remove_peer(peer);
}

class WanPeerApiProbe : public rti::routing::processor::NoOpProcessor {
public:
    void on_start(Route& route) override
    {
        auto reader = route.input<DynamicData>("wan_input").dds_data_reader();
        check_peer_api(reader.subscriber().participant());

        auto writer = route.output<DynamicData>("wan_output").dds_data_writer();
        check_peer_api(writer.publisher().participant());
    }

    void on_periodic_action(Route& route) override
    {
        auto reader = route.input<DynamicData>("wan_input").dds_data_reader();
        const auto participant = reader.subscriber().participant();
        const auto handles = dds::domain::discovered_participants(participant);
        for (const auto& handle : handles) {
            const auto data =
                    dds::domain::discovered_participant_data(participant, handle);
            (void) data;
        }
    }
};

}

extern "C" rti::routing::processor::Processor *create_peer_api_probe()
{
    return new WanPeerApiProbe;
}

extern "C" void delete_peer_api_probe(
        rti::routing::processor::Processor *processor)
{
    delete processor;
}
