using System.Runtime.InteropServices;
using System.Text;

namespace Anima.Launcher;

internal static class Credentials
{
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct Credential
    {
        public uint Flags, Type;
        public string TargetName, Comment;
        public System.Runtime.InteropServices.ComTypes.FILETIME LastWritten;
        public uint CredentialBlobSize;
        public IntPtr CredentialBlob;
        public uint Persist, AttributeCount;
        public IntPtr Attributes;
        public string TargetAlias, UserName;
    }
    [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)] private static extern bool CredWrite(ref Credential credential, uint flags);
    [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)] private static extern bool CredRead(string target, uint type, uint flags, out IntPtr credential);
    [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)] private static extern bool CredDelete(string target, uint type, uint flags);
    [DllImport("advapi32.dll")] private static extern void CredFree(IntPtr buffer);
    public static string? Read(string host)
    {
        if (!CredRead("AnimaRandomStudio/" + host, 1, 0, out var pointer)) return null;
        try { var c = Marshal.PtrToStructure<Credential>(pointer); return Marshal.PtrToStringUni(c.CredentialBlob, (int)c.CredentialBlobSize / 2); }
        finally { CredFree(pointer); }
    }
    public static void Save(string host, string token)
    {
        if (string.IsNullOrWhiteSpace(token)) { CredDelete("AnimaRandomStudio/" + host, 1, 0); return; }
        var pointer = Marshal.StringToCoTaskMemUni(token);
        try
        {
            var credential = new Credential { Type = 1, TargetName = "AnimaRandomStudio/" + host, UserName = host, CredentialBlob = pointer, CredentialBlobSize = (uint)Encoding.Unicode.GetByteCount(token), Persist = 2, Comment = "Anima model download" };
            if (!CredWrite(ref credential, 0)) throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
        }
        finally { Marshal.ZeroFreeCoTaskMemUnicode(pointer); }
    }
}
