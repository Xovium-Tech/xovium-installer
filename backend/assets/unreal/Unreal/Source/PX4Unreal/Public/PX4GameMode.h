#pragma once

#include "CoreMinimal.h"
#include "GameFramework/GameModeBase.h"
#include "PX4GameMode.generated.h"

UCLASS()
class PX4UNREAL_API APX4GameMode : public AGameModeBase
{
    GENERATED_BODY()

public:
    APX4GameMode();
    virtual void Tick(float DeltaSeconds) override;

protected:
    virtual void BeginPlay() override;

private:
    void BuildEnvironment();
    void SpawnVehicle();
    UPROPERTY(Transient) TObjectPtr<class APX4Terrain> StreamedTerrain;
    double TerrainDeadline=0;
    bool bWaitingForTerrain=false;
};
