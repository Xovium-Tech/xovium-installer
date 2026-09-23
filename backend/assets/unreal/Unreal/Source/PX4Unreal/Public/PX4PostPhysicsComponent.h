#pragma once

#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "PX4PostPhysicsComponent.generated.h"

UCLASS()
class PX4UNREAL_API UPX4PostPhysicsComponent : public UActorComponent
{
    GENERATED_BODY()

public:
    UPX4PostPhysicsComponent();
    virtual void TickComponent(float DeltaTime, ELevelTick TickType,
        FActorComponentTickFunction* ThisTickFunction) override;
};
