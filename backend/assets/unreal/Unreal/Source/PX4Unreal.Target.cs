using UnrealBuildTool;
using System.Collections.Generic;

public class PX4UnrealTarget : TargetRules
{
    public PX4UnrealTarget(TargetInfo Target) : base(Target)
    {
        Type = TargetType.Game;
        DefaultBuildSettings = BuildSettingsVersion.V5;
        IncludeOrderVersion = EngineIncludeOrderVersion.Unreal5_6;
        ExtraModuleNames.Add("PX4Unreal");
    }
}
