#include "PX4Terrain.h"
#include "Materials/MaterialInterface.h"
#include "HAL/PlatformMisc.h"
#include "Misc/CommandLine.h"
#include "Misc/Parse.h"
bool APX4Terrain::LoadSharedConfig(const FString& Path) {
    if(!Super::LoadSharedConfig(Path)) return false;
    if(auto* Existing=LoadObject<UMaterialInterface>(nullptr,TEXT("/Game/Worlds/M_Terrain.M_Terrain"))) TerrainMaterial=Existing;
    FPlatformMisc::SetEnvironmentVar(TEXT("PX4_HOME_LAT"),*FString::Printf(TEXT("%.12f"),Latitude));
    FPlatformMisc::SetEnvironmentVar(TEXT("PX4_HOME_LON"),*FString::Printf(TEXT("%.12f"),Longitude));
    FPlatformMisc::SetEnvironmentVar(TEXT("PX4_HOME_ALT"),*FString::Printf(TEXT("%.8f"),Altitude));
    return true;
}
void APX4Terrain::BeginPlay() {
    if(FParse::Param(FCommandLine::Get(),TEXT("PX4NoTerrain"))) bStreamTerrain=false;
    Super::BeginPlay();
}
