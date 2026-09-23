#include "PX4Plane.h"

#include "Camera/CameraComponent.h"
#include "Components/BoxComponent.h"
#include "Components/StaticMeshComponent.h"
#include "Engine/Engine.h"
#include "Engine/StaticMesh.h"
#include "Engine/World.h"
#include "GameFramework/SpringArmComponent.h"
#include "HAL/IConsoleManager.h"
#include "HAL/PlatformMisc.h"
#include "HAL/PlatformProcess.h"
#include "HAL/PlatformTime.h"
#include "Materials/MaterialInstanceDynamic.h"
#include "Materials/MaterialInterface.h"
#include "Misc/App.h"
#include "Misc/CommandLine.h"
#include "Misc/Parse.h"
#include "PhysicalMaterials/PhysicalMaterial.h"
#include "PX4PostPhysicsComponent.h"
#include "UObject/ConstructorHelpers.h"

DEFINE_LOG_CATEGORY_STATIC(LogPX4Plane, Log, All);

namespace
{
ue_px4::Vec3 PolarToNed(const FVector& Value, double Scale = 0.01)
{
    return {Value.X * Scale, Value.Y * Scale, -Value.Z * Scale};
}

ue_px4::Vec3 AxialToFrd(const FVector& Value)
{
    return {-Value.X, -Value.Y, Value.Z};
}
}

APX4Plane::APX4Plane()
{
    PrimaryActorTick.bCanEverTick = true;
    PrimaryActorTick.TickGroup = TG_PrePhysics;
    AutoPossessPlayer = EAutoReceiveInput::Disabled;

    Body = CreateDefaultSubobject<UBoxComponent>(TEXT("ChaosAirframe"));
    RootComponent = Body;
    Body->SetMobility(EComponentMobility::Movable);
    Body->InitBoxExtent(FVector(70.0, 90.0, 12.0));
    Body->SetCollisionProfileName(TEXT("PhysicsActor"));
    Body->SetSimulatePhysics(false);
    Body->SetEnableGravity(true);
    Body->SetLinearDamping(0.0f);
    Body->SetAngularDamping(0.02f);
    Body->BodyInstance.bUseCCD = true;
    Body->BodyInstance.InertiaTensorScale = FVector(0.40, 0.48, 0.40);

    static ConstructorHelpers::FObjectFinder<UStaticMesh> Cube(
        TEXT("/Engine/BasicShapes/Cube.Cube"));
    static ConstructorHelpers::FObjectFinder<UStaticMesh> Sphere(
        TEXT("/Engine/BasicShapes/Sphere.Sphere"));
    AddVisual(TEXT("Fuselage"), Sphere.Object, FVector(0, 0, 2), FVector(1.45, 0.19, 0.20));
    AddVisual(TEXT("MainWing"), Cube.Object, FVector(0, 0, 8), FVector(0.30, 1.80, 0.035));
    AddVisual(TEXT("Tailplane"), Cube.Object, FVector(-56, 0, 12), FVector(0.17, 0.65, 0.025));
    AddVisual(TEXT("Fin"), Cube.Object, FVector(-57, 0, 24), FVector(0.22, 0.025, 0.30));
    AddVisual(TEXT("Propeller"), Cube.Object, FVector(74, 0, 2), FVector(0.025, 0.50, 0.028));
    AddVisual(TEXT("LeftSkid"), Sphere.Object, FVector(10, -25, -8), FVector(0.15, 0.07, 0.08));
    AddVisual(TEXT("RightSkid"), Sphere.Object, FVector(10, 25, -8), FVector(0.15, 0.07, 0.08));

    CameraArm = CreateDefaultSubobject<USpringArmComponent>(TEXT("FollowArm"));
    CameraArm->SetupAttachment(Body);
    CameraArm->TargetArmLength = 900.0f;
    CameraArm->SetRelativeRotation(FRotator(-14.0, 0.0, 0.0));
    CameraArm->bInheritPitch = false;
    CameraArm->bInheritRoll = false;
    CameraArm->bDoCollisionTest = false;
    CameraArm->bEnableCameraLag = false;
    Camera = CreateDefaultSubobject<UCameraComponent>(TEXT("FollowCamera"));
    Camera->SetupAttachment(CameraArm, USpringArmComponent::SocketName);
    Camera->FieldOfView = 70.0f;
    SensorTick = CreateDefaultSubobject<UPX4PostPhysicsComponent>(TEXT("PX4Sensors"));
}

UStaticMeshComponent* APX4Plane::AddVisual(const TCHAR* Name, UStaticMesh* Mesh,
    const FVector& Position, const FVector& Scale)
{
    UStaticMeshComponent* Visual = CreateDefaultSubobject<UStaticMeshComponent>(Name);
    Visual->SetupAttachment(Body);
    Visual->SetMobility(EComponentMobility::Movable);
    Visual->SetStaticMesh(Mesh);
    Visual->SetRelativeLocation(Position);
    Visual->SetRelativeScale3D(Scale);
    Visual->SetCollisionEnabled(ECollisionEnabled::NoCollision);
    Visuals.Add(Visual);
    return Visual;
}

void APX4Plane::BeginPlay()
{
    Super::BeginPlay();
    if (GetWorld()->WorldType == EWorldType::PIE)
    {
        UE_LOG(LogPX4Plane, Error, TEXT("PX4 simulation requires standalone -game; use scripts/run_unreal.sh."));
        SetActorTickEnabled(false);
        SensorTick->SetComponentTickEnabled(false);
        return;
    }

    int32 Port = 4560;
    double Hz = 250.0;
    FParse::Value(FCommandLine::Get(), TEXT("PX4Port="), Port);
    FParse::Value(FCommandLine::Get(), TEXT("SimHz="), Hz);
    FParse::Value(FCommandLine::Get(), TEXT("SimSpeed="), Speed);
    FParse::Value(FCommandLine::Get(), TEXT("PX4Timeout="), ControlTimeout);
    FParse::Value(FCommandLine::Get(), TEXT("PX4StartupTimeout="), StartupTimeout);
    if (Port < 1024 || Port > 65535 || !FMath::IsFinite(Hz) || Hz != 250 ||
        !FMath::IsFinite(Speed) || Speed < 0 || !FMath::IsFinite(ControlTimeout) ||
        ControlTimeout <= 0 || !FMath::IsFinite(StartupTimeout) || StartupTimeout <= 0)
    {
        Fail(TEXT("Invalid PX4Port, SimHz (must be 250), SimSpeed (>=0), or timeout (>0)."));
        return;
    }
    StepUs = static_cast<uint64>(FMath::RoundToDouble(1000000.0 / Hz));
    StepSeconds = static_cast<double>(StepUs) / 1000000.0;
    FApp::SetFixedDeltaTime(StepSeconds);
    FApp::SetUseFixedTimeStep(true);
    if (GEngine)
    {
        GEngine->bSmoothFrameRate = false;
    }
    if (IConsoleVariable* VSync = IConsoleManager::Get().FindConsoleVariable(TEXT("r.VSync")))
    {
        VSync->Set(0, ECVF_SetByCode);
    }
    if (IConsoleVariable* MaxFps = IConsoleManager::Get().FindConsoleVariable(TEXT("t.MaxFPS")))
    {
        MaxFps->Set(0.0f, ECVF_SetByCode);
    }

    GroundContactMaterial = NewObject<UPhysicalMaterial>(this);
    GroundContactMaterial->Friction = 0.02f;
    GroundContactMaterial->StaticFriction = 0.02f;
    GroundContactMaterial->Restitution = 0.0f;
    GroundContactMaterial->bOverrideFrictionCombineMode = true;
    GroundContactMaterial->FrictionCombineMode = EFrictionCombineMode::Min;
    Body->SetPhysMaterialOverride(GroundContactMaterial);
    Body->SetMassOverrideInKg(NAME_None, static_cast<float>(ue_px4::mass_kg), true);

    UMaterialInterface* Base = LoadObject<UMaterialInterface>(nullptr,
        TEXT("/Engine/BasicShapes/BasicShapeMaterial.BasicShapeMaterial"));
    if (Base)
    {
        for (int32 Index = 0; Index < Visuals.Num(); ++Index)
        {
            UMaterialInstanceDynamic* Material = UMaterialInstanceDynamic::Create(Base, this);
            const FLinearColor Color = (Index == 1 || Index == 3)
                ? FLinearColor(0.03f, 0.22f, 0.75f) : FLinearColor(0.85f, 0.87f, 0.90f);
            Material->SetVectorParameterValue(TEXT("Color"), Color);
            Visuals[Index]->SetMaterial(0, Material);
        }
    }

    std::string Error;
    if (!Link.listen(Port, Error))
    {
        Fail(FString::Printf(TEXT("MAVLink listen failed: %s"), UTF8_TO_TCHAR(Error.c_str())));
        return;
    }
    UE_LOG(LogPX4Plane, Display, TEXT("PX4_SIM_LISTENING port=%d; waiting for PX4 SITL"), Port);
    if (!Link.accept_client(StartupTimeout, Error))
    {
        Fail(FString::Printf(TEXT("PX4 connection failed: %s"), UTF8_TO_TCHAR(Error.c_str())));
        return;
    }
    if (!Bootstrap())
    {
        return;
    }
    PreviousVelocity = FVector::ZeroVector;
    Body->SetSimulatePhysics(true);
    Body->WakeAllRigidBodies();
    WallStart = FPlatformTime::Seconds();
    bReady = true;
    UE_LOG(LogPX4Plane, Display, TEXT("PX4_SIM_READY hz=%.3f speed=%.2f mass=%.2f kg"),
        1.0 / StepSeconds, Speed, ue_px4::mass_kg);
}

bool APX4Plane::Bootstrap()
{
    const double Deadline = FPlatformTime::Seconds() + StartupTimeout;
    const ue_px4::State FrozenState = ReadState(FVector::ZeroVector);
    std::string Error;
    while (FPlatformTime::Seconds() < Deadline)
    {
        if (!Link.send_state(FrozenState, TimeUs, Error))
        {
            Fail(FString::Printf(TEXT("Initial sensor send failed: %s"), UTF8_TO_TCHAR(Error.c_str())));
            return false;
        }
        const auto Result = Link.receive_controls(Controls, StepSeconds, Error);
        if (Result == ue_px4::MavlinkLink::ReceiveResult::Controls)
        {
            bHaveControls = true;
            return true;
        }
        if (Result != ue_px4::MavlinkLink::ReceiveResult::Timeout)
        {
            Fail(FString::Printf(TEXT("PX4 bootstrap failed: %s"), UTF8_TO_TCHAR(Error.c_str())));
            return false;
        }
        TimeUs += StepUs;
    }
    Fail(TEXT("Timed out waiting for PX4 HIL_ACTUATOR_CONTROLS during startup."));
    return false;
}

ue_px4::State APX4Plane::ReadState(const FVector& AccelerationWorldCm) const
{
    ue_px4::State State;
    State.position_ned_m = PolarToNed(Body->GetComponentLocation());
    State.velocity_ned_mps = PolarToNed(Body->GetPhysicsLinearVelocity());
    State.acceleration_ned_mps2 = PolarToNed(AccelerationWorldCm);
    const FQuat Rotation = Body->GetComponentQuat();
    const FVector RatesBody = Rotation.UnrotateVector(Body->GetPhysicsAngularVelocityInRadians());
    State.angular_velocity_frd_radps = AxialToFrd(RatesBody);
    State.attitude_frd_to_ned = {Rotation.W, -Rotation.X, -Rotation.Y, Rotation.Z};
    return State;
}

void APX4Plane::Tick(float DeltaSeconds)
{
    Super::Tick(DeltaSeconds);
    if (!bReady || bFailed)
    {
        return;
    }
    if (!FMath::IsNearlyEqual(static_cast<double>(DeltaSeconds), StepSeconds, 0.000001))
    {
        Fail(FString::Printf(TEXT("World dt %.9f differs from sensor dt %.9f; run standalone -game without time dilation."),
            DeltaSeconds, StepSeconds));
        return;
    }
    if (!bHaveControls)
    {
        std::string Error;
        if (Link.receive_controls(Controls, ControlTimeout, Error) !=
            ue_px4::MavlinkLink::ReceiveResult::Controls)
        {
            Fail(FString::Printf(TEXT("PX4 controls stopped: %s"), UTF8_TO_TCHAR(Error.c_str())));
            return;
        }
    }
    bHaveControls = false;

    if (Speed > 0)
    {
        const double TargetWall = WallStart + static_cast<double>(CompletedSteps) * StepSeconds / Speed;
        const double Remaining = TargetWall - FPlatformTime::Seconds();
        if (Remaining > 0)
        {
            FPlatformProcess::SleepNoStats(static_cast<float>(Remaining));
        }
    }

    const FQuat Rotation = Body->GetComponentQuat();
    const FVector VelocityBody = Rotation.UnrotateVector(Body->GetPhysicsLinearVelocity());
    const FVector RatesBody = Rotation.UnrotateVector(Body->GetPhysicsAngularVelocityInRadians());
    AppliedControls = ue_px4::filter_controls(AppliedControls, Controls, StepSeconds);
    const auto Wrench = ue_px4::aerodynamic_wrench(PolarToNed(VelocityBody),
        AxialToFrd(RatesBody), AppliedControls);
    const FVector ForceBody(Wrench.force_frd_n.x * 100.0,
        Wrench.force_frd_n.y * 100.0, -Wrench.force_frd_n.z * 100.0);
    const FVector TorqueBody(-Wrench.torque_frd_nm.x * 10000.0,
        -Wrench.torque_frd_nm.y * 10000.0, Wrench.torque_frd_nm.z * 10000.0);
    Body->AddForce(Rotation.RotateVector(ForceBody));
    Body->AddTorqueInRadians(Rotation.RotateVector(TorqueBody));
    bStepPending = true;
}

void APX4Plane::PostPhysicsTick(float                 )
{
    if (!bReady || bFailed || !bStepPending)
    {
        return;
    }
    bStepPending = false;
    const FVector Velocity = Body->GetPhysicsLinearVelocity();
    const FVector Acceleration = (Velocity - PreviousVelocity) / StepSeconds;
    PreviousVelocity = Velocity;
    if (Velocity.ContainsNaN() || Acceleration.ContainsNaN() ||
        Body->GetComponentLocation().ContainsNaN() || Body->GetComponentQuat().ContainsNaN())
    {
        Fail(TEXT("Chaos returned a non-finite aircraft state."));
        return;
    }
    TimeUs += StepUs;
    ++CompletedSteps;
    std::string Error;
    if (!Link.send_state(ReadState(Acceleration), TimeUs, Error))
    {
        Fail(FString::Printf(TEXT("Sensor send failed: %s"), UTF8_TO_TCHAR(Error.c_str())));
        return;
    }
    if (CompletedSteps % static_cast<uint64>(FMath::Max(1.0, 5.0 / StepSeconds)) == 0)
    {
        const double WallElapsed = FPlatformTime::Seconds() - WallStart;
        UE_LOG(LogPX4Plane, Display, TEXT("PX4_SIM time=%.2f s real_time_factor=%.2f airspeed=%.2f m/s armed=%d"),
            static_cast<double>(CompletedSteps) * StepSeconds,
            static_cast<double>(CompletedSteps) * StepSeconds / FMath::Max(0.001, WallElapsed),
            Velocity.Size() * 0.01, Controls.armed ? 1 : 0);
    }
}

void APX4Plane::Fail(const FString& Reason)
{
    bFailed = true;
    bReady = false;
    Body->SetSimulatePhysics(false);
    Link.close();
    UE_LOG(LogPX4Plane, Error, TEXT("PX4_SIM_FAILED: %s"), *Reason);
    FPlatformMisc::RequestExitWithStatus(false, 1);
}

void APX4Plane::EndPlay(const EEndPlayReason::Type EndPlayReason)
{
    bReady = false;
    Link.close();
    Super::EndPlay(EndPlayReason);
}
