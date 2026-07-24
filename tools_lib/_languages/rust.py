# Auto-registered language: rust
# compiler: persisted, version: auto

def handler(code):
    import tempfile, subprocess, os, random
    tag = "jv_" + str(random.randint(10000,99999))
    src = os.path.join(tempfile.gettempdir(), tag + ".rs")
    exe = os.path.join(tempfile.gettempdir(), tag + ".exe")

    jarvis_root = os.environ.get("JARVIS_ROOT", "D:\\Javis")
    rust_root = os.path.join(jarvis_root, "tools", "rust")
    toolchain = os.path.join(rust_root, "rustup", "toolchains",
        "stable-x86_64-pc-windows-gnu")
    rustc = os.path.join(toolchain, "bin", "rustc.exe")
    mingw_bin = os.path.join(jarvis_root, "tools", "mingw32", "bin")

    with open(src, "wb") as f:
        f.write(code.encode("utf-8"))

    env = os.environ.copy()
    env["CARGO_HOME"] = os.path.join(rust_root, "cargo")
    env["RUSTUP_HOME"] = os.path.join(rust_root, "rustup")
    env["PATH"] = mingw_bin + os.pathsep + toolchain + "\\bin" + os.pathsep + env.get("PATH", "")
    env["RUSTC_LINKER"] = "x86_64-w64-mingw32-gcc"

    cr = subprocess.run([rustc, src, "-o", exe,
        "-C", "opt-level=0", "--target", "x86_64-pc-windows-gnu"],
        capture_output=True, text=True, timeout=120, env=env)
    if cr.returncode != 0:
        return "[编译错误]\n" + cr.stderr.strip()[:1500]

    rr = subprocess.run([exe], capture_output=True, text=True, timeout=15, env=env)
    for p in [src, exe]:
        try: os.unlink(p)
        except: pass
    out = rr.stdout.strip()
    err = rr.stderr.strip()
    if out: return out[:2000]
    if err: return "[stderr] " + err[:1900]
    return str(rr.returncode)

if __name__ == "__main__":
    import sys
    r = handler(sys.stdin.read())
    print(r)


if __name__ == "__main__":
    import sys
    r = handler(sys.stdin.read())
    print(r)


if __name__ == "__main__":
    import sys
    r = handler(sys.stdin.read())
    print(r)


if __name__ == "__main__":
    import sys
    r = handler(sys.stdin.read())
    print(r)


if __name__ == "__main__":
    import sys
    r = handler(sys.stdin.read())
    print(r)


if __name__ == "__main__":
    import sys
    r = handler(sys.stdin.read())
    print(r)


if __name__ == "__main__":
    import sys
    r = handler(sys.stdin.read())
    print(r)


if __name__ == "__main__":
    import sys
    r = handler(sys.stdin.read())
    print(r)


if __name__ == "__main__":
    import sys
    r = handler(sys.stdin.read())
    print(r)


if __name__ == "__main__":
    import sys
    r = handler(sys.stdin.read())
    print(r)


if __name__ == "__main__":
    import sys
    r = handler(sys.stdin.read())
    print(r)


if __name__ == "__main__":
    import sys
    r = handler(sys.stdin.read())
    print(r)


if __name__ == "__main__":
    import sys
    r = handler(sys.stdin.read())
    print(r)


if __name__ == "__main__":
    import sys
    r = handler(sys.stdin.read())
    print(r)


if __name__ == "__main__":
    import sys
    r = handler(sys.stdin.read())
    print(r)


if __name__ == "__main__":
    import sys
    r = handler(sys.stdin.read())
    print(r)


if __name__ == "__main__":
    import sys
    r = handler(sys.stdin.read())
    print(r)


if __name__ == "__main__":
    import sys
    r = handler(sys.stdin.read())
    print(r)
