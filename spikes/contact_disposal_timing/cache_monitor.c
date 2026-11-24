#include <ndds/ndds_c.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

static void *observe(void *unused)
{
    (void) unused;
    const char *path = getenv("CONTACT_CACHE_LOG");
    const char *stop = getenv("CONTACT_CACHE_STOP");
    const char *start = getenv("CONTACT_CACHE_START");
    if (!path || !stop || !start) return NULL;
    FILE *output = fopen(path, "w");
    if (!output) return NULL;
    while (access(start, F_OK) != 0 && access(stop, F_OK) != 0)
        usleep(50000);
    DDS_DataWriter *reported_writer = NULL;
    while (access(stop, F_OK) != 0) {
        DDS_DomainParticipant *participant = DDS_DomainParticipantFactory_lookup_participant(
            DDS_DomainParticipantFactory_get_instance(), 212);
        struct DDS_PublisherSeq publishers = DDS_SEQUENCE_INITIALIZER;
        if (participant && DDS_DomainParticipant_get_publishers(
                participant, &publishers) == DDS_RETCODE_OK) {
            for (int i = 0; i < DDS_PublisherSeq_get_length(&publishers); ++i) {
                struct DDS_DataWriterSeq writers = DDS_SEQUENCE_INITIALIZER;
                DDS_Publisher *publisher = *DDS_PublisherSeq_get_reference(&publishers, i);
                if (DDS_Publisher_get_all_datawriters(publisher, &writers) != DDS_RETCODE_OK) {
                    fprintf(output, "{\"error\":\"get_all_datawriters\"}\n");
                    continue;
                }
                for (int j = 0; j < DDS_DataWriterSeq_get_length(&writers); ++j) {
                    DDS_DataWriter *writer = *DDS_DataWriterSeq_get_reference(&writers, j);
                    if (strcmp(DDS_TopicDescription_get_name(DDS_Topic_as_topicdescription(
                            DDS_DataWriter_get_topic(writer))), "State"))
                        continue;
                    if (reported_writer != writer) {
                        struct DDS_DataWriterQos qos = DDS_DataWriterQos_INITIALIZER;
                        if (DDS_DataWriter_get_qos(writer, &qos) == DDS_RETCODE_OK) {
                            fprintf(output, "{\"event\":\"writer_qos\",\"cleanup_sec\":%d,"
                                    "\"cleanup_nanosec\":%u,\"durability_kind\":%d}\n",
                                    qos.writer_data_lifecycle.autopurge_disposed_instances_delay.sec,
                                    qos.writer_data_lifecycle.autopurge_disposed_instances_delay.nanosec,
                                    qos.durability.kind);
                            reported_writer = writer;
                        } else {
                            fprintf(output, "{\"error\":\"writer_get_qos\"}\n");
                        }
                        DDS_DataWriterQos_finalize(&qos);
                    }
                    struct DDS_DataWriterCacheStatus cache = DDS_DataWriterCacheStatus_INITIALIZER;
                    struct DDS_ReliableWriterCacheChangedStatus reliable =
                        DDS_ReliableWriterCacheChangedStatus_INITIALIZER;
                    struct DDS_ReliableReaderActivityChangedStatus activity =
                        DDS_ReliableReaderActivityChangedStatus_INITIALIZER;
                    struct DDS_PublicationMatchedStatus matches =
                        DDS_PublicationMatchedStatus_INITIALIZER;
                    struct timespec now;
                    clock_gettime(CLOCK_MONOTONIC, &now);
                    if (DDS_DataWriter_get_datawriter_cache_status(writer, &cache) != DDS_RETCODE_OK
                            || DDS_DataWriter_get_reliable_writer_cache_changed_status(
                                writer, &reliable) != DDS_RETCODE_OK
                            || DDS_DataWriter_get_reliable_reader_activity_changed_status(
                                writer, &activity) != DDS_RETCODE_OK
                            || DDS_DataWriter_get_publication_matched_status(
                                writer, &matches) != DDS_RETCODE_OK) {
                        fprintf(output, "{\"error\":\"writer_cache_status\"}\n");
                        continue;
                    }
                    fprintf(output, "{\"monotonic_ns\":%lld,\"disposed\":%lld,"
                            "\"alive\":%lld,\"samples\":%lld,\"unacknowledged\":%d,"
                            "\"active_readers\":%d,\"matched_readers\":%d}\n",
                            (long long)now.tv_sec * 1000000000LL + now.tv_nsec,
                            (long long)cache.disposed_instance_count,
                            (long long)cache.alive_instance_count,
                            (long long)cache.sample_count, reliable.unacknowledged_sample_count,
                            activity.active_count, matches.current_count);
                }
                DDS_DataWriterSeq_finalize(&writers);
            }
        }
        DDS_PublisherSeq_finalize(&publishers);
        fflush(output);
        usleep(50000);
    }
    fprintf(output, "{\"event\":\"monitor_stopped\"}\n");
    fclose(output);
    return NULL;
}

__attribute__((constructor)) static void start_observer(void)
{
    if (!getenv("CONTACT_CACHE_LOG")) return;
    pthread_t thread;
    if (pthread_create(&thread, NULL, observe, NULL) == 0)
        pthread_detach(thread);
    else
        fprintf(stderr, "CONTACT_CACHE_ERROR: pthread_create failed\n");
}
