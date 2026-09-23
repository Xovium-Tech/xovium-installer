using UnrealBuildTool;
using System.Collections.Generic;

public class PX4UnrealEditorTarget : TargetRules
{
    public PX4UnrealEditorTarget(TargetInfo Target) : base(Target)
    {
        Type = TargetType.Editor;
        DefaultBuildSettings = BuildSettingsVersion.V5;
        IncludeOrderVersion = EngineIncludeOrderVersion.Unreal5_6;
        ExtraModuleNames.Add("PX4Unreal");
    }
}
