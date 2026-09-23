#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Pawn.h"
#include "ue_px4/flight_model.hpp"
#include "ue_px4/mavlink_link.hpp"
#include "PX4Plane.generated.h"

class UBoxComponent;
class UCameraComponent;
class UPhysicalMaterial;
class UPX4PostPhysicsComponent;
class USpringArmComponent;
class UStaticMesh;
class UStaticMeshComponent;

UCLASS()
class PX4UNREAL_API APX4Plane : public APawn
{
    GENERATED_BODY()

public:
    APX4Plane();
    virtual void Tick(float DeltaSeconds) override;
    virtual void EndPlay(const EEndPlayReason::Type EndPlayReason) override;
    void PostPhysicsTick(float DeltaSeconds);

protected:
    virtual void BeginPlay() override;

private:
    UStaticMeshComponent* AddVisual(const TCHAR* Name, UStaticMesh* Mesh,
        const FVector& Position, const FVector& Scale);
    ue_px4::State ReadState(const FVector& AccelerationWorldCm) const;
    bool Bootstrap();
    void Fail(const FString& Reason);

    UPROPERTY() TObjectPtr<UBoxComponent> Body;
    UPROPERTY() TObjectPtr<USpringArmComponent> CameraArm;
    UPROPERTY() TObjectPtr<UCameraComponent> Camera;
    UPROPERTY() TObjectPtr<UPX4PostPhysicsComponent> SensorTick;
    UPROPERTY() TObjectPtr<UPhysicalMaterial> GroundContactMaterial;
    UPROPERTY() TArray<TObjectPtr<UStaticMeshComponent>> Visuals;

    ue_px4::MavlinkLink Link;
    ue_px4::Controls Controls;
    ue_px4::Controls AppliedControls;
    FVector PreviousVelocity = FVector::ZeroVector;
    uint64 TimeUs = 1000000;
    uint64 StepUs = 4000;
    uint64 CompletedSteps = 0;
    double StepSeconds = 0.004;
    double Speed = 1.0;
    double ControlTimeout = 30.0;
    double StartupTimeout = 120.0;
    double WallStart = 0.0;
    bool bReady = false;
    bool bHaveControls = false;
    bool bStepPending = false;
    bool bFailed = false;
};
