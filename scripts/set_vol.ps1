Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;

[Guid("5CDF2C82-841E-4546-9722-0CF74078229A"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IAudioEndpointVolume {
    int SetMasterVolumeLevelScalar(float f, Guid g);
    int GetMasterVolumeLevelScalar(out float f);
}

[Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IMMDeviceEnumerator {
    int GetDefaultAudioEndpoint(int dataFlow, int role, out IMMDevice endpoint);
}

[Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IMMDevice {
    int Activate(ref Guid id, int clsCtx, int activationParams, out IAudioEndpointVolume aev);
}

[ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")]
public class MMDeviceEnumeratorComObject { }

public class Vol {
    public static void Set(int level) {
        var enumerator = (IMMDeviceEnumerator)new MMDeviceEnumeratorComObject();
        IMMDevice dev;
        enumerator.GetDefaultAudioEndpoint(0, 0, out dev);
        IAudioEndpointVolume aev;
        Guid iid = new Guid("5CDF2C82-841E-4546-9722-0CF74078229A");
        dev.Activate(ref iid, 0, 0, out aev);
        aev.SetMasterVolumeLevelScalar(level / 100f, Guid.Empty);
    }
}
"@
[Vol]::Set(10)
