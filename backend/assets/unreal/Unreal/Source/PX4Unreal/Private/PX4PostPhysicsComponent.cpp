#include "PX4PostPhysicsComponent.h"
#include "PX4Plane.h"

UPX4PostPhysicsComponent::UPX4PostPhysicsComponent()
{
    PrimaryComponentTick.bCanEverTick = true;
    PrimaryComponentTick.TickGroup = TG_PostPhysics;
}

void UPX4PostPhysicsComponent::TickComponent(float DeltaTime, ELevelTick TickType,
    FActorComponentTickFunction* ThisTickFunction)
{
    Super::TickComponent(DeltaTime, TickType, ThisTickFunction);
    if (APX4Plane* Plane = Cast<APX4Plane>(GetOwner()))
    {
        Plane->PostPhysicsTick(DeltaTime);
    }
}
