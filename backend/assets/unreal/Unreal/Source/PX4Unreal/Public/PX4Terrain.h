#pragma once
#include "DynamicTerrainActor.h"
#include "PX4Terrain.generated.h"
// Preserve serialized /Script/PX4Unreal.PX4Terrain and inherited property names.
UCLASS(BlueprintType, Blueprintable, meta=(DisplayName="Dynamic Terrain"))
class PX4UNREAL_API APX4Terrain : public ADynamicTerrainActor {
    GENERATED_BODY()
public:
    bool LoadSharedConfig(const FString& Path);
    virtual void BeginPlay() override;
};
