using System;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Threading;

namespace Javis.NativeAudio
{
    internal enum EDataFlow { Render, Capture, All }
    internal enum ERole { Console, Multimedia, Communications }

    [Flags]
    internal enum AudioClientStreamFlags : uint
    {
        None = 0,
        AUDCLNT_STREAMFLAGS_LOOPBACK = 0x00020000
    }

    [ComImport]
    [Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")]
    internal class MMDeviceEnumerator { }

    [ComImport]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    [Guid("A95664D2-9614-4F35-A746-DE8DB63617E6")]
    internal interface IMMDeviceEnumerator
    {
        [PreserveSig]
        int EnumAudioEndpoints(EDataFlow dataFlow, uint stateMask, out IntPtr devices);
        [PreserveSig]
        int GetDefaultAudioEndpoint(EDataFlow dataFlow, ERole role, out IMMDevice endpoint);
        [PreserveSig]
        int GetDevice([MarshalAs(UnmanagedType.LPWStr)] string id, out IMMDevice device);
        [PreserveSig]
        int RegisterEndpointNotificationCallback(IntPtr client);
        [PreserveSig]
        int UnregisterEndpointNotificationCallback(IntPtr client);
    }

    [ComImport]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    [Guid("D666063F-1587-4E43-81F1-B948E807363F")]
    internal interface IMMDevice
    {
        [PreserveSig]
        int Activate(ref Guid iid, uint clsctx, IntPtr activationParams,
            [MarshalAs(UnmanagedType.IUnknown)] out object instance);
        [PreserveSig]
        int OpenPropertyStore(uint access, out IntPtr properties);
        [PreserveSig]
        int GetId([MarshalAs(UnmanagedType.LPWStr)] out string id);
        [PreserveSig]
        int GetState(out uint state);
    }

    [ComImport]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    [Guid("1CB9AD4C-DBFA-4c32-B178-C2F568A703B2")]
    internal interface IAudioClient
    {
        [PreserveSig]
        int Initialize(
            int shareMode,
            AudioClientStreamFlags streamFlags,
            long bufferDuration,
            long periodicity,
            IntPtr format,
            IntPtr sessionGuid);
        [PreserveSig]
        int GetBufferSize(out uint bufferFrames);
        [PreserveSig]
        int GetStreamLatency(out long latency);
        [PreserveSig]
        int GetCurrentPadding(out uint paddingFrames);
        [PreserveSig]
        int IsFormatSupported(int shareMode, IntPtr format, out IntPtr closestMatch);
        [PreserveSig]
        int GetMixFormat(out IntPtr format);
        [PreserveSig]
        int GetDevicePeriod(out long defaultPeriod, out long minimumPeriod);
        [PreserveSig]
        int Start();
        [PreserveSig]
        int Stop();
        [PreserveSig]
        int Reset();
        [PreserveSig]
        int SetEventHandle(IntPtr eventHandle);
        [PreserveSig]
        int GetService(ref Guid serviceGuid, [MarshalAs(UnmanagedType.IUnknown)] out object service);
    }

    [ComImport]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    [Guid("C8ADBD64-E71E-48a0-A4DE-185C395CD317")]
    internal interface IAudioCaptureClient
    {
        [PreserveSig]
        int GetBuffer(
            out IntPtr data,
            out uint frames,
            out uint flags,
            out ulong devicePosition,
            out ulong qpcPosition);
        [PreserveSig]
        int ReleaseBuffer(uint frames);
        [PreserveSig]
        int GetNextPacketSize(out uint frames);
    }

    internal static class Program
    {
        private const uint CLSCTX_ALL = 23;
        private const uint AUDCLNT_BUFFERFLAGS_SILENT = 0x2;

        [DllImport("ole32.dll")]
        private static extern int CoInitializeEx(IntPtr reserved, uint coInit);

        [DllImport("ole32.dll")]
        private static extern void CoTaskMemFree(IntPtr pointer);

        private static void Check(int result)
        {
            if (result < 0) Marshal.ThrowExceptionForHR(result);
        }

        private static byte[] ReadFormat(IntPtr format, out ushort blockAlign)
        {
            blockAlign = (ushort)Marshal.ReadInt16(format, 12);
            ushort extra = (ushort)Marshal.ReadInt16(format, 16);
            int length = 18 + extra;
            byte[] bytes = new byte[length];
            Marshal.Copy(format, bytes, 0, length);
            return bytes;
        }

        private static void WriteWave(string output, byte[] format, byte[] audio)
        {
            string parent = Path.GetDirectoryName(Path.GetFullPath(output));
            if (!Directory.Exists(parent)) Directory.CreateDirectory(parent);
            using (FileStream file = new FileStream(output, FileMode.Create, FileAccess.Write, FileShare.Read))
            using (BinaryWriter writer = new BinaryWriter(file))
            {
                writer.Write(new char[] { 'R', 'I', 'F', 'F' });
                writer.Write(4 + 8 + format.Length + 8 + audio.Length);
                writer.Write(new char[] { 'W', 'A', 'V', 'E' });
                writer.Write(new char[] { 'f', 'm', 't', ' ' });
                writer.Write(format.Length);
                writer.Write(format);
                writer.Write(new char[] { 'd', 'a', 't', 'a' });
                writer.Write(audio.Length);
                writer.Write(audio);
            }
        }

        private static int Capture(string output, string stopFile, double maxSeconds)
        {
            int initialized = CoInitializeEx(IntPtr.Zero, 0);
            if (initialized < 0 && initialized != unchecked((int)0x80010106))
                Marshal.ThrowExceptionForHR(initialized);

            IMMDeviceEnumerator enumerator = (IMMDeviceEnumerator)new MMDeviceEnumerator();
            IMMDevice endpoint;
            Check(enumerator.GetDefaultAudioEndpoint(EDataFlow.Render, ERole.Multimedia, out endpoint));

            Guid audioClientGuid = typeof(IAudioClient).GUID;
            object audioClientObject;
            Check(endpoint.Activate(ref audioClientGuid, CLSCTX_ALL, IntPtr.Zero, out audioClientObject));
            IAudioClient audioClient = (IAudioClient)audioClientObject;

            IntPtr formatPointer;
            Check(audioClient.GetMixFormat(out formatPointer));
            ushort blockAlign;
            byte[] format = ReadFormat(formatPointer, out blockAlign);
            try
            {
                Check(audioClient.Initialize(
                    0,
                    AudioClientStreamFlags.AUDCLNT_STREAMFLAGS_LOOPBACK,
                    10000000,
                    0,
                    formatPointer,
                    IntPtr.Zero));
            }
            finally
            {
                CoTaskMemFree(formatPointer);
            }

            Guid captureGuid = typeof(IAudioCaptureClient).GUID;
            object captureObject;
            Check(audioClient.GetService(ref captureGuid, out captureObject));
            IAudioCaptureClient captureClient = (IAudioCaptureClient)captureObject;

            MemoryStream captured = new MemoryStream();
            Stopwatch elapsed = Stopwatch.StartNew();
            Check(audioClient.Start());
            try
            {
                while (!File.Exists(stopFile) && elapsed.Elapsed.TotalSeconds < maxSeconds)
                {
                    uint packetFrames;
                    Check(captureClient.GetNextPacketSize(out packetFrames));
                    while (packetFrames > 0)
                    {
                        IntPtr data;
                        uint frames;
                        uint flags;
                        ulong devicePosition;
                        ulong qpcPosition;
                        Check(captureClient.GetBuffer(
                            out data,
                            out frames,
                            out flags,
                            out devicePosition,
                            out qpcPosition));
                        int byteCount = checked((int)(frames * blockAlign));
                        byte[] chunk = new byte[byteCount];
                        if ((flags & AUDCLNT_BUFFERFLAGS_SILENT) == 0 && data != IntPtr.Zero)
                            Marshal.Copy(data, chunk, 0, byteCount);
                        captured.Write(chunk, 0, chunk.Length);
                        Check(captureClient.ReleaseBuffer(frames));
                        Check(captureClient.GetNextPacketSize(out packetFrames));
                    }
                    Thread.Sleep(5);
                }
            }
            finally
            {
                audioClient.Stop();
            }

            WriteWave(output, format, captured.ToArray());
            return 0;
        }

        public static int Main(string[] args)
        {
            try
            {
                if (args.Length < 2)
                {
                    Console.Error.WriteLine("usage: javis-wasapi-loopback.exe OUTPUT STOP_FILE [MAX_SECONDS]");
                    return 2;
                }
                double maxSeconds = args.Length >= 3 ? Math.Max(1, Math.Min(60, double.Parse(args[2]))) : 30;
                return Capture(args[0], args[1], maxSeconds);
            }
            catch (Exception error)
            {
                Console.Error.WriteLine(error.ToString());
                return 1;
            }
        }
    }
}
