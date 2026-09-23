#pragma once
struct Px4Release { const char* version; int jetty; bool legacyModels; bool nativeBridges; };
inline constexpr Px4Release px4Releases[] = {
    {"1.16.0", 1, false, true},
    {"1.17.0", 2, false, true},
    {"1.16.2", 1, false, true},
    {"1.16.1", 1, false, true},
    {"1.15.4", 0, true, true},
    {"1.15.3", 0, true, true},
    {"1.15.2", 0, true, true},
    {"1.15.1", 0, true, true},
    {"1.15.0", 0, true, true},
};
