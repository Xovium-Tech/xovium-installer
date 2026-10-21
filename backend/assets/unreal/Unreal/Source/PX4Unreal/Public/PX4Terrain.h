#pragma once
#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "Engine/EngineTypes.h"
#include "PX4Terrain.generated.h"
class UProceduralMeshComponent;
class UMaterialInterface;
class UTexture2D;
class UPrimitiveComponent;
struct xov_terrain_page;
UCLASS(BlueprintType, Blueprintable, meta=(DisplayName="Dynamic Terrain"))
class PX4UNREAL_API APX4Terrain : public AActor {
    GENERATED_BODY()
public:
    APX4Terrain();
    bool LoadSharedConfig(const FString& Path);
    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category="Terrain") TObjectPtr<UProceduralMeshComponent> Terrain;
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Terrain") bool bStreamTerrain = true;
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Transient, Category="Terrain") bool bPreviewInEditor = false;
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Terrain") TObjectPtr<AActor> FollowActor;
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Terrain") TObjectPtr<UPrimitiveComponent> FallbackGround;
    // Hide these surfaces before downloading, retaining collision until terrain arrives.
    // Tag custom runway/ground components in the editor to include them.
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Terrain") FName FallbackSurfaceTag = TEXT("XoviumFallbackSurface");
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Terrain") TObjectPtr<UMaterialInterface> TerrainMaterial;
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Terrain", meta=(ClampMin="-85",ClampMax="85")) double Latitude = 47.397742;
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Terrain", meta=(ClampMin="-180",ClampMax="180")) double Longitude = 8.545594;
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Terrain") double Altitude = 489.4;
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Terrain", meta=(ClampMin="100",ClampMax="15000")) double RadiusM = 1500;
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Terrain", meta=(ClampMin="10",ClampMax="17")) int32 Zoom = 14;
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Terrain", meta=(ClampMin="8",ClampMax="128")) int32 CellsPerTile = 32;
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Terrain|Imagery", meta=(ClampMin="256",ClampMax="4096")) int32 TextureSize = 2048;
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Terrain|Imagery", meta=(ClampMin="10",ClampMax="20")) int32 ImageryDetailZoom = 17;
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Terrain|Imagery", meta=(ClampMin="100",ClampMax="15000",Units="m")) double ImageryDetailRadiusM = 1000;
    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category="Terrain|Imagery") int32 HighestImageryZoom = 0;
    // Empty uses the existing Gazebo cache; explicit paths can isolate custom providers.
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Terrain|Cache") FDirectoryPath CacheDirectory;
    UFUNCTION(BlueprintPure, Category="Terrain|Cache") FString GetTerrainCacheDirectory() const;
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Terrain") FString ImageryURL;
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Terrain") FString ElevationURL;
    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category="Terrain") int32 LoadedPages = 0;
    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category="Terrain") int32 TextureUpdates = 0;
    UFUNCTION(CallInEditor, BlueprintCallable, Category="Terrain") void RefreshTerrain();
    UFUNCTION(CallInEditor, BlueprintCallable, Category="Terrain") void StopTerrain();
    virtual void BeginPlay() override;
    virtual void Tick(float) override;
    virtual bool ShouldTickIfViewportsOnly() const override {return bPreviewInEditor;}
    virtual void EndPlay(const EEndPlayReason::Type) override;
    virtual void Destroyed() override;
    virtual void BeginDestroy() override;
    void ApplyPage(const struct xov_terrain_page& Page);
    // Trace only cooked terrain collision; fallback ground is never readiness.
    bool TraceGround(const FVector& Position, FHitResult& Hit) const;
private:
    struct FFallbackState {
        TWeakObjectPtr<UPrimitiveComponent> Component;
        bool bVisible;
        ECollisionEnabled::Type Collision;
    };
    TArray<FFallbackState> FallbackStates;
    void HideFallbackSurfaces(bool bDisableCollision);
    UPROPERTY(Transient) TArray<TObjectPtr<UTexture2D>> Textures;
    void* Runtime = nullptr;
};
