"""Render the canonical public edge for a validated deployment IPv4 address."""
import ipaddress
import os
from pathlib import Path
import tempfile

def prepare(deploy_dir: Path, override=None):
    runtime=deploy_dir/"runtime"
    if runtime.is_symlink() or not runtime.is_dir():
        raise ValueError("UNSAFE_PUBLIC_RUNTIME")
    config=runtime/"public-ip"
    value=override if override is not None else (config.read_text(encoding="ascii").strip() if config.exists() else "120.24.187.205")
    address=ipaddress.ip_address(value)
    if address.version!=4 or not address.is_global:
        raise ValueError("PUBLIC_IPV4_REQUIRED")
    template=(deploy_dir/"tls/edge-tls.conf").read_text(encoding="utf-8")
    rendered=template.replace("https://120.24.187.205$request_uri","https://"+str(address)+"$request_uri")
    destination=runtime/"edge-tls.template.conf"
    if destination.is_symlink():
        raise ValueError("PUBLIC_TEMPLATE_SYMLINK_REJECTED")
    descriptor,temporary=tempfile.mkstemp(prefix=".public-edge-",dir=runtime)
    try:
        with os.fdopen(descriptor,"w",encoding="utf-8",newline="\n") as stream:stream.write(rendered)
        os.chmod(temporary,0o644);os.replace(temporary,destination)
    finally:
        if os.path.exists(temporary):os.unlink(temporary)
    return str(address)

if __name__=="__main__":
    print(prepare(Path(__file__).resolve().parents[1],os.environ.get("DROP_PUBLIC_IP")))
