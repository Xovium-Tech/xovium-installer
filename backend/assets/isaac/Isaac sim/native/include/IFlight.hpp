#pragma once
#include <carb/Interface.h>
namespace px4isaac {
struct IFlight {
    CARB_PLUGIN_INTERFACE("px4isaac::IFlight", 0, 1);
    void (*tick)();
};
}
