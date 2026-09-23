#include "Modules/ModuleManager.h"
#include "Math/UnrealMathUtility.h"
#include "Misc/App.h"
#include "Misc/CommandLine.h"
#include "Misc/Parse.h"

class FPX4UnrealModule : public FDefaultGameModuleImpl
{
public:
    virtual void StartupModule() override
    {
        if (FApp::IsGame() || FParse::Param(FCommandLine::Get(), TEXT("game")))
        {
            double Hz = 250.0;
            FParse::Value(FCommandLine::Get(), TEXT("SimHz="), Hz);
            if (FMath::IsFinite(Hz) && Hz == 250.0)
            {
                const double Step = FMath::RoundToDouble(1000000.0 / Hz) / 1000000.0;
                FApp::SetFixedDeltaTime(Step);
                FApp::SetUseFixedTimeStep(true);
            }
        }
    }
};

IMPLEMENT_PRIMARY_GAME_MODULE(FPX4UnrealModule, PX4Unreal, "PX4Unreal");
