#include <dds/dds.hpp>
#include <ndds/ndds_c.h>

#include <iostream>
#include <stdexcept>

int main(int argc, char **argv) {
    try {
        if (argc < 3) {
            throw std::invalid_argument("usage: type_size XML TYPE [TYPE ...]");
        }
        dds::core::QosProvider provider(argv[1]);
        for (int i = 2; i < argc; ++i) {
            const auto &type = provider->type(argv[i]);
            DDS_ExceptionCode_t error = DDS_NO_EXCEPTION_CODE;
            const auto size = DDS_TypeCode_get_type_object_serialized_size(
                    &type.native(), &error);
            if (error != DDS_NO_EXCEPTION_CODE) {
                throw std::runtime_error("TypeObject size failed");
            }
            std::cout << argv[i] << " " << size << "\n";
        }
    } catch (const std::exception &error) {
        std::cerr << error.what() << "\n";
        return 1;
    }
}
