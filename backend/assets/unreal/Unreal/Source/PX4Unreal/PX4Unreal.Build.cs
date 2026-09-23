using UnrealBuildTool;
using System.IO;

public class PX4Unreal : ModuleRules
{
    public PX4Unreal(ReadOnlyTargetRules Target) : base(Target)
    {
        PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;
        CppStandard = CppStandardVersion.Cpp20;
        bUseUnity = false;
        PublicDependencyModuleNames.AddRange(new[] {
            "Core", "CoreUObject", "Engine", "PhysicsCore"
        });
        string RepositoryRoot = Path.GetFullPath(Path.Combine(ModuleDirectory, "../../.."));
        PublicIncludePaths.Add(Path.Combine(RepositoryRoot, "native/include"));
        PublicSystemIncludePaths.Add(Path.Combine(RepositoryRoot, "ThirdParty/mavlink/include"));
        ExternalDependencies.Add(Path.Combine(RepositoryRoot, "native/src/mavlink_link.cpp"));
        if (Target.Platform != UnrealTargetPlatform.Linux)
        {
            throw new BuildException("PX4Unreal currently supports Linux only.");
        }
    }
}
