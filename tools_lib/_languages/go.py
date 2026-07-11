# Auto-registered language: go
# compiler: persisted, version: auto

def handler(code):
    import tempfile, subprocess, os, random
    tag = "jv_" + str(random.randint(10000,99999))
    src = os.path.join(tempfile.gettempdir(), tag + ".go")
    exe = os.path.join(tempfile.gettempdir(), tag + ".exe")
    with open(src, "wb") as f:
        f.write(code.encode("utf-8"))
    cr = subprocess.run(["go", "build", "-o", exe, src], capture_output=True, text=True, timeout=60)
    if cr.returncode != 0: return f"[编译失败]"
    rr = subprocess.run([exe], capture_output=True, text=True, timeout=15)
    for p in [src, exe]:
        try: os.unlink(p)
        except: pass
    return (rr.stdout.strip() or rr.stderr.strip() or str(rr.returncode))[:2000]


if __name__ == "__main__":
    import sys
    r = handler(sys.stdin.read())
    print(r)
