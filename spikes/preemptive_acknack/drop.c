#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/uio.h>

/* Deliberately lose one original user DATA, never discovery, heartbeat or repair.
 * The fixed writer entity is selected after discovery via an owned control file. */
static int dropped;
static int lose(const void *buffer, size_t length)
{
    const unsigned char *p = buffer;
    const char *path = getenv("SPIKE_DROP_CONTROL");
    if (dropped || !path || length < 20 || memcmp(p, "RTPS", 4))
        return 0;
    FILE *control = fopen(path, "r");
    if (!control)
        return 0;
    unsigned entity;
    unsigned long sequence;
    int configured = fscanf(control, "%x %lu", &entity, &sequence) == 2;
    fclose(control);
    if (!configured)
        return 0;
    for (size_t at = 20; at + 4 <= length;) {
        int little = p[at + 1] & 1;
        size_t size = little ? p[at + 2] | (p[at + 3] << 8)
                             : (p[at + 2] << 8) | p[at + 3];
        size_t end = size ? at + 4 + size : length;
        if (end > length || end <= at)
            break;
        if ((p[at] == 0x15 || p[at] == 0x14) && end >= at + 32) {
            const unsigned char *body = p + at + 4;
            unsigned id = (body[8] << 24) | (body[9] << 16) |
                          (body[10] << 8) | body[11];
            uint32_t low;
            memcpy(&low, body + (p[at] == 0x14 ? 24 : 16), 4);
            if (!little)
                low = __builtin_bswap32(low);
            if (id == entity && low == sequence) {
                dropped = 1;
                fprintf(stderr, "SPIKE_DROP entity=%08x sequence=%lu\n", entity, sequence);
                fflush(stderr);
                return 1;
            }
        }
        at = end;
    }
    return 0;
}

ssize_t sendto(int socket, const void *buffer, size_t length, int flags,
               const struct sockaddr *address, socklen_t address_length)
{
    static ssize_t (*real_sendto)(int, const void *, size_t, int,
                                 const struct sockaddr *, socklen_t);
    if (!real_sendto)
        real_sendto = dlsym(RTLD_NEXT, "sendto");
    if (lose(buffer, length))
        return length;
    return real_sendto(socket, buffer, length, flags, address, address_length);
}

ssize_t sendmsg(int socket, const struct msghdr *message, int flags)
{
    static ssize_t (*real_sendmsg)(int, const struct msghdr *, int);
    if (!real_sendmsg)
        real_sendmsg = dlsym(RTLD_NEXT, "sendmsg");
    unsigned char buffer[65536];
    size_t length = 0;
    for (size_t i = 0; i < message->msg_iovlen; ++i) {
        size_t n = message->msg_iov[i].iov_len;
        if (n > sizeof(buffer) - length)
            return real_sendmsg(socket, message, flags);
        memcpy(buffer + length, message->msg_iov[i].iov_base, n);
        length += n;
    }
    if (lose(buffer, length))
        return length;
    return real_sendmsg(socket, message, flags);
}
