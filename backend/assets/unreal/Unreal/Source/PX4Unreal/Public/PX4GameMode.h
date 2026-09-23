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

protected:
    virtual void BeginPlay() override;

private:
    void BuildEnvironment();
};
