#include "PX4Terrain.h"
#include "ue_px4/media.h"
#include "ProceduralMeshComponent.h"
#include "Engine/Texture2D.h"
#include "ImageCore.h"
#include "Engine/World.h"
#include "Materials/MaterialInstanceDynamic.h"
#include "Misc/Paths.h"
#include "HAL/PlatformProcess.h"
#include "HAL/PlatformMisc.h"
#include "Misc/CommandLine.h"
#include "Misc/Parse.h"
#include "Misc/FileHelper.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
APX4Terrain::APX4Terrain() {
    PrimaryActorTick.bCanEverTick=true;PrimaryActorTick.TickInterval=.2f;
    Terrain=CreateDefaultSubobject<UProceduralMeshComponent>(TEXT("StreamedGround"));RootComponent=Terrain;
    Terrain->SetCanEverAffectNavigation(false);
    Terrain->bUseAsyncCooking=false;
    Terrain->bUseComplexAsSimpleCollision=true;
    Terrain->SetCollisionProfileName(TEXT("BlockAll"));
}
bool APX4Terrain::LoadSharedConfig(const FString& Path) {
    FString Text; TSharedPtr<FJsonObject> Json;
    if(!FFileHelper::LoadFileToString(Text,*Path) || !FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Text),Json) || !Json.IsValid()) return false;
    double Schema=0,ZoomValue=0,Radius=0;
    const TSharedPtr<FJsonObject>* Origin=nullptr;
    if(!Json->TryGetNumberField(TEXT("schema"),Schema) || Schema!=1 ||
       !Json->TryGetObjectField(TEXT("origin"),Origin) ||
       !(*Origin)->TryGetNumberField(TEXT("latitude"),Latitude) ||
       !(*Origin)->TryGetNumberField(TEXT("longitude"),Longitude) ||
       !(*Origin)->TryGetNumberField(TEXT("altitude"),Altitude) ||
       !Json->TryGetNumberField(TEXT("zoom"),ZoomValue) || !Json->TryGetNumberField(TEXT("radius"),Radius) ||
       !Json->TryGetStringField(TEXT("cache"),CacheDirectory.Path) ||
       !Json->TryGetStringField(TEXT("imagery_url"),ImageryURL) || !Json->TryGetStringField(TEXT("elevation_url"),ElevationURL)) return false;
    if(!FMath::IsFinite(Latitude) || FMath::Abs(Latitude)>85 || !FMath::IsFinite(Longitude) || FMath::Abs(Longitude)>180 ||
       !FMath::IsFinite(Altitude) || ZoomValue<10 || ZoomValue>17 || Radius<1 || Radius>3 ||
       FMath::FloorToDouble(ZoomValue)!=ZoomValue || FMath::FloorToDouble(Radius)!=Radius) return false;
    if(ElevationURL.StartsWith(TEXT("https://s3.amazonaws.com/elevation-tiles-prod/terrarium/")) && ZoomValue>15) return false;
    Zoom=static_cast<int32>(ZoomValue); ImageryDetailZoom=Zoom;
    RadiusM=(Radius+.5)*40075016.686*FMath::Cos(FMath::DegreesToRadians(Latitude))/FMath::Pow(2.,Zoom);
    ImageryDetailRadiusM=RadiusM;
    if(FPaths::IsRelative(CacheDirectory.Path)) CacheDirectory.Path=FPaths::ConvertRelativePathToFull(FPaths::GetPath(Path),CacheDirectory.Path);
    TerrainMaterial=LoadObject<UMaterialInterface>(nullptr,TEXT("/Game/Worlds/M_Terrain.M_Terrain"));
    if(!TerrainMaterial) UE_LOG(LogTemp,Warning,TEXT("Shared terrain material missing; terrain geometry/collision remains active."));
    FPlatformMisc::SetEnvironmentVar(TEXT("PX4_HOME_LAT"),*FString::Printf(TEXT("%.12f"),Latitude));
    FPlatformMisc::SetEnvironmentVar(TEXT("PX4_HOME_LON"),*FString::Printf(TEXT("%.12f"),Longitude));
    FPlatformMisc::SetEnvironmentVar(TEXT("PX4_HOME_ALT"),*FString::Printf(TEXT("%.8f"),Altitude));
    return true;
}
void APX4Terrain::StopTerrain() {
    bPreviewInEditor=false;
    xov_terrain_destroy(Runtime);Runtime=nullptr;
    for(const auto& State:FallbackStates) {
        if(auto* Component=State.Component.Get()) {
            Component->SetVisibility(State.bVisible);
            Component->SetCollisionEnabled(State.Collision);
        }
    }
    FallbackStates.Empty();
    if(LoadedPages>0) {
        if(IsValid(Terrain)) Terrain->ClearAllMeshSections();
        Textures.Empty();LoadedPages=0;TextureUpdates=0;HighestImageryZoom=0;
    }
}
void APX4Terrain::HideFallbackSurfaces(bool bDisableCollision) {
    if(!IsValid(FallbackGround)) return;
    TArray<UPrimitiveComponent*> Components;
    if(auto* Owner=FallbackGround->GetOwner()) Owner->GetComponents(Components);
    Components.AddUnique(FallbackGround.Get());
    for(auto* Component:Components) {
        if(Component==Terrain || (Component!=FallbackGround && !Component->ComponentHasTag(FallbackSurfaceTag))) continue;
        if(!FallbackStates.ContainsByPredicate([Component](const FFallbackState& State){return State.Component.Get()==Component;}))
            FallbackStates.Add({Component,Component->IsVisible(),Component->GetCollisionEnabled()});
        Component->SetVisibility(false);
        if(bDisableCollision) Component->SetCollisionEnabled(ECollisionEnabled::NoCollision);
    }
}
FString APX4Terrain::GetTerrainCacheDirectory() const {
    FString Directory=CacheDirectory.Path;
    if(Directory.IsEmpty()) Directory=FPlatformMisc::GetEnvironmentVariable(TEXT("XOVIUM_TERRAIN_CACHE"));
    // Custom URL caches retain project isolation unless explicitly configured.
    if(Directory.IsEmpty() && (!ImageryURL.IsEmpty() || !ElevationURL.IsEmpty()))
        Directory=FPaths::ProjectSavedDir()/TEXT("TerrainCache");
    if(Directory.IsEmpty()) Directory=FString(FPlatformProcess::UserHomeDir())/TEXT(".cache/gz_dynamic_terrain");
    if(Directory.StartsWith(TEXT("~/"))) Directory=FString(FPlatformProcess::UserHomeDir())/Directory.Mid(2);
    return FPaths::ConvertRelativePathToFull(Directory);
}
void APX4Terrain::BeginPlay() {
    Super::BeginPlay();
    if(bStreamTerrain && !FParse::Param(FCommandLine::Get(),TEXT("PX4NoTerrain")))
        HideFallbackSurfaces(false);
}
void APX4Terrain::RefreshTerrain() {
    StopTerrain();
    if(IsRunningCommandlet()) return;
    HideFallbackSurfaces(false);
    const FString Cache=GetTerrainCacheDirectory();
    UE_LOG(LogTemp,Display,TEXT("XOVIUM_TERRAIN_CACHE: %s"),*Cache);
    FTCHARToUTF8 C(*Cache),I(*ImageryURL),E(*ElevationURL);
    xov_terrain_config Config{Latitude,Longitude,Altitude,RadiusM,Zoom,CellsPerTile,C.Get(),I.Get(),E.Get()};
    const xov_terrain_quality Quality{TextureSize,ImageryDetailZoom,ImageryDetailRadiusM};
    Runtime=xov_terrain_create_with_quality(&Config,&Quality);
    bPreviewInEditor=GetWorld()&&!GetWorld()->IsGameWorld();
    if(!Runtime) UE_LOG(LogTemp,Error,TEXT("Xovium terrain configuration is invalid."));
}
void APX4Terrain::Tick(float Dt) {
    Super::Tick(Dt);
    if(!bStreamTerrain || (GetWorld()&&!GetWorld()->IsGameWorld()&&!bPreviewInEditor)) {StopTerrain();return;}
    if(!Runtime) RefreshTerrain();
    if(!Runtime) return;
    const FVector P=FollowActor?FollowActor->GetActorLocation():GetActorLocation();
    xov_terrain_update(Runtime,P.X*.01,P.Y*.01,P.Z*.01);
    xov_terrain_poll(Runtime,[](void* User,const xov_terrain_page* Page){static_cast<APX4Terrain*>(User)->ApplyPage(*Page);},this);
}
bool APX4Terrain::TraceGround(const FVector& Position, FHitResult& Hit) const {
    if(LoadedPages==0 || !IsValid(Terrain) || !Terrain->IsQueryCollisionEnabled() || !Terrain->IsPhysicsCollisionEnabled()
        || Terrain->GetCollisionResponseToChannel(ECC_PhysicsBody)!=ECR_Block) return false;
    const FBox Bounds=Terrain->Bounds.GetBox();
    const FVector Start(Position.X,Position.Y,Bounds.Max.Z+100);
    const FVector End(Position.X,Position.Y,Bounds.Min.Z-100);
    // Direct component traces report touch hits even for BlockAll. The collision
    // settings above establish that the hit actually supports the airframe.
    return Terrain->LineTraceComponent(Hit,Start,End,FCollisionQueryParams(SCENE_QUERY_STAT(SpawnTerrain),true));
}
void APX4Terrain::ApplyPage(const xov_terrain_page& P) {
    if(P.reset) {Terrain->ClearAllMeshSections();Textures.Empty();LoadedPages=0;TextureUpdates=0;HighestImageryZoom=0;}
    if(!P.texture_only) {
        TArray<FVector> Vertices,Normals;TArray<FVector2D> UV;TArray<int32> Indices;
        for(size_t i=0;i<P.vertices;++i) {
            Vertices.Add(GetActorTransform().InverseTransformPosition(FVector(P.positions[i*3]*100,P.positions[i*3+1]*100,P.positions[i*3+2]*100)));
            Normals.Add(GetActorTransform().InverseTransformVectorNoScale(FVector(P.normals[i*3],P.normals[i*3+1],P.normals[i*3+2])));
            UV.Add(FVector2D(P.uv[i*2],P.uv[i*2+1]));
        }
        // Geographic already emits north/east/up: X north, Y east, Z up, matching
        // Unreal's handedness. Its grid triangles face up without another reversal.
        for(size_t i=0;i<P.indices;++i) Indices.Add(P.triangles[i]);
        Terrain->CreateMeshSection_LinearColor(P.section,Vertices,Indices,Normals,UV,{}, {},true);
        ++LoadedPages;
        HideFallbackSurfaces(true);
    }
    if(P.rgb&&P.width>0&&P.height>0&&TerrainMaterial) {
        UTexture2D* Texture=UTexture2D::CreateTransient(P.width,P.height,PF_B8G8R8A8);
        if(Texture) {
            Texture->SRGB=true;
            Texture->NeverStream=true;
            Texture->Filter=TF_Trilinear;
            Texture->AddressX=TA_Clamp;Texture->AddressY=TA_Clamp;
#if WITH_EDITORONLY_DATA
            Texture->MipGenSettings=TMGS_LeaveExistingMips;
#endif
            FImage Level(P.width,P.height,ERawImageFormat::BGRA8,EGammaSpace::sRGB);
            auto Pixels=Level.AsBGRA8();
            for(int32 i=0;i<P.width*P.height;++i) Pixels[i]=FColor(P.rgb[3*i],P.rgb[3*i+1],P.rgb[3*i+2],255);
            int32 MipIndex=0;
            for(;;) {
                auto& Mips=Texture->GetPlatformData()->Mips;
                if(MipIndex>0) Mips.Add(new FTexture2DMipMap());
                auto& Mip=Mips[MipIndex++];Mip.SizeX=Level.SizeX;Mip.SizeY=Level.SizeY;Mip.SizeZ=1;
                Mip.BulkData.Lock(LOCK_READ_WRITE);
                void* Data=Mip.BulkData.Realloc(Level.RawData.Num());
                FMemory::Memcpy(Data,Level.RawData.GetData(),Level.RawData.Num());
                Mip.BulkData.Unlock();
                if(Level.SizeX==1 && Level.SizeY==1) break;
                FImage Next;
                FImageCore::ResizeImageAllocDest(Level,Next,FMath::Max(1,Level.SizeX/2),FMath::Max(1,Level.SizeY/2),FImageCore::EResizeImageFilter::Box);
                Level=MoveTemp(Next);
            }
            Texture->UpdateResource();
            HighestImageryZoom=FMath::Max(HighestImageryZoom,P.imagery_zoom);
            if(Textures.Num()<=P.section) Textures.SetNum(P.section+1);
            Textures[P.section]=Texture;
            auto* Material=UMaterialInstanceDynamic::Create(TerrainMaterial,this);Material->SetTextureParameterValue(TEXT("TerrainTexture"),Texture);Terrain->SetMaterial(P.section,Material);
            if(P.texture_only) ++TextureUpdates;
        }
    }
}
void APX4Terrain::EndPlay(const EEndPlayReason::Type Reason) {StopTerrain();Super::EndPlay(Reason);}
void APX4Terrain::Destroyed() {StopTerrain();Super::Destroyed();}
void APX4Terrain::BeginDestroy() {StopTerrain();Super::BeginDestroy();}
