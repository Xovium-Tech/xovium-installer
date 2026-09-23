#include "PX4GameMode.h"

#include "Components/DirectionalLightComponent.h"
#include "Components/SkyAtmosphereComponent.h"
#include "Components/SkyLightComponent.h"
#include "Components/StaticMeshComponent.h"
#include "Engine/DirectionalLight.h"
#include "Engine/SkyLight.h"
#include "Engine/StaticMesh.h"
#include "Engine/StaticMeshActor.h"
#include "Engine/World.h"
#include "GameFramework/PlayerController.h"
#include "GameFramework/WorldSettings.h"
#include "Materials/MaterialInstanceDynamic.h"
#include "Materials/MaterialInterface.h"
#include "PX4Plane.h"

APX4GameMode::APX4GameMode()
{
    DefaultPawnClass = nullptr;
    bStartPlayersAsSpectators = false;
}

void APX4GameMode::BeginPlay()
{
    Super::BeginPlay();
    GetWorld()->GetWorldSettings()->KillZ = -10000000.0f;
    GetWorld()->GetWorldSettings()->bGlobalGravitySet = true;
    GetWorld()->GetWorldSettings()->GlobalGravityZ = -980.665f;
    BuildEnvironment();

    FActorSpawnParameters Parameters;
    Parameters.SpawnCollisionHandlingOverride = ESpawnActorCollisionHandlingMethod::AlwaysSpawn;
    APX4Plane* Plane = GetWorld()->SpawnActor<APX4Plane>(FVector(0, 0, 16),
        FRotator::ZeroRotator, Parameters);
    if (APlayerController* Player = GetWorld()->GetFirstPlayerController())
    {
        if (Plane)
        {
            Player->Possess(Plane);
            Player->SetViewTarget(Plane);
        }
    }
}

void APX4GameMode::BuildEnvironment()
{
    UStaticMesh* Cube = LoadObject<UStaticMesh>(nullptr, TEXT("/Engine/BasicShapes/Cube.Cube"));
    UMaterialInterface* BaseMaterial = LoadObject<UMaterialInterface>(nullptr,
        TEXT("/Engine/BasicShapes/BasicShapeMaterial.BasicShapeMaterial"));
    auto Box = [&](const FVector& Location, const FVector& Scale,
        const FLinearColor& Color, bool Collision)
    {
        AStaticMeshActor* Actor = GetWorld()->SpawnActor<AStaticMeshActor>(Location, FRotator::ZeroRotator);
        UStaticMeshComponent* Mesh = Actor->GetStaticMeshComponent();
        Mesh->SetMobility(EComponentMobility::Movable);
        Mesh->SetStaticMesh(Cube);
        Mesh->SetWorldScale3D(Scale);
        Mesh->SetCollisionEnabled(Collision ? ECollisionEnabled::QueryAndPhysics : ECollisionEnabled::NoCollision);
        Mesh->SetCollisionObjectType(ECC_WorldStatic);
        Mesh->SetCollisionResponseToAllChannels(ECR_Block);
        if (BaseMaterial)
        {
            UMaterialInstanceDynamic* Material = UMaterialInstanceDynamic::Create(BaseMaterial, Actor);
            Material->SetVectorParameterValue(TEXT("Color"), Color);
            Material->SetScalarParameterValue(TEXT("Roughness"), 0.95f);
            Mesh->SetMaterial(0, Material);
        }
        Mesh->SetMobility(EComponentMobility::Static);
    };

    Box(FVector(0, 0, -10), FVector(4000, 4000, 0.2), FLinearColor(0.10f, 0.20f, 0.075f), true);
    Box(FVector(30000, 0, 1.5), FVector(800, 24, 0.03), FLinearColor(0.075f, 0.08f, 0.09f), true);
    for (int32 Index = 0; Index < 40; ++Index)
    {
        Box(FVector(-8000 + Index * 2000, 0, 3.2), FVector(8, 0.25, 0.002), FLinearColor::White, false);
    }
    Box(FVector(30000, -1100, 3.2), FVector(790, 0.15, 0.002), FLinearColor::White, false);
    Box(FVector(30000, 1100, 3.2), FVector(790, 0.15, 0.002), FLinearColor::White, false);

    ADirectionalLight* Sun = GetWorld()->SpawnActor<ADirectionalLight>(
        FVector(0, 0, 20000), FRotator(-40, -30, 0));
    UDirectionalLightComponent* SunComponent = Cast<UDirectionalLightComponent>(Sun->GetLightComponent());
    SunComponent->SetMobility(EComponentMobility::Movable);
    SunComponent->SetIntensity(3.0f);
    SunComponent->SetAtmosphereSunLight(true);

    AActor* AtmosphereActor = GetWorld()->SpawnActor<AActor>();
    USkyAtmosphereComponent* Atmosphere = NewObject<USkyAtmosphereComponent>(AtmosphereActor);
    AtmosphereActor->SetRootComponent(Atmosphere);
    AtmosphereActor->AddInstanceComponent(Atmosphere);
    Atmosphere->RegisterComponent();

    ASkyLight* Sky = GetWorld()->SpawnActor<ASkyLight>();
    Sky->GetLightComponent()->SetMobility(EComponentMobility::Movable);
    Sky->GetLightComponent()->SetIntensity(0.6f);
    Sky->GetLightComponent()->RecaptureSky();
}
