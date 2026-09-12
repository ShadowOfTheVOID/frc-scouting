"""The Windows Firewall rule that phones need, offered once instead of guessed at.

Windows asks about a new listening program with a popup that appears behind the
black window, says nothing about phones, and is dismissed by roughly everybody.
Dismissing it is silent: the hub runs, the admin panel works, the laptop's own
browser works, and every phone in the building times out with no explanation
anywhere.  It has been the most common setup failure this app has, and it is one
`netsh` line to fix.

So the hub asks, in the window somebody is already looking at, and says what the
answer buys.  Three rules hold everywhere below:

  * **Never on any other platform, and never without a person there.**  macOS
    and Linux do not need it, and a service or a CI runner has nobody to answer.
  * **Never twice.**  A "no" is remembered; the rule existing is itself the
    memory of a "yes".
  * **Never fatal.**  Every path here ends with the hub starting anyway, and a
    failure prints the command to run by hand rather than a stack trace.

The rule is the narrowest one that works: inbound, TCP, this port only, private
networks only.  A venue network is a private network to Windows; a coffee shop
is a public one, and the hub has no business being reachable there.
"""
import platform
import re
import subprocess
import sys

RULE = "FRC-Scouting-Hub"          # no spaces, so nothing here has to quote it
#: Remembered in the hub's settings, so a "no" is a "no" and not a question
#: asked at the start of every practice session.
DECLINED = "firewallDeclined"


def relevant():
    """Whether asking makes any sense on this machine, right now."""
    return platform.system() == "Windows" and sys.stdin.isatty() and sys.stdout.isatty()


def _netsh(*args):
    """Run netsh and return (ok, output).  Never raises."""
    try:
        r = subprocess.run(["netsh", "advfirewall", "firewall", *args],
                           capture_output=True, text=True, timeout=20)
        return r.returncode == 0, (r.stdout or "") + (r.stderr or "")
    except Exception as e:
        return False, str(e)


#: The port as its own number, not as a run of digits inside another one.
#: `str(port) in out` said yes for `--port 605` against a rule holding 6059,
#: which is the false "allowed" this check exists to catch - and netsh prints
#: several numbers per rule, so there is plenty to collide with.
def _port_in(out, port):
    return re.search(r"(?<!\d)%d(?!\d)" % int(port), out or "") is not None


def exists(port):
    """Whether our rule is already in place for this port.

    Checked by name and then by port, because a rule left over from a run on a
    different port would otherwise read as "allowed" while phones still cannot
    connect - which is the exact failure this module exists to remove.

    Matched on the number rather than on a label: `netsh` prints its output in
    the language Windows is installed in, and "LocalPort" is not one of the
    words that survives that.
    """
    ok, out = _netsh("show", "rule", "name=" + RULE)
    return ok and _port_in(out, port)


def add(port):
    """Ask Windows for the rule, elevating.  True only if it is really there.

    Elevation goes through PowerShell's Start-Process because that is the only
    way to raise a UAC prompt from a non-elevated process and wait for it.  The
    return value is not its exit code but a fresh look at the firewall: the
    person may have clicked No, and a hub that says "allowed" when nothing was
    allowed is worse than one that says nothing.
    """
    args = ",".join([
        "'advfirewall'", "'firewall'", "'add'", "'rule'",
        f"'name={RULE}'", "'dir=in'", "'action=allow'", "'protocol=TCP'",
        f"'localport={port}'", "'profile=private'",
    ])
    script = f"Start-Process -FilePath netsh -Verb RunAs -Wait -WindowStyle Hidden -ArgumentList {args}"
    try:
        subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                        "-Command", script], capture_output=True, timeout=120)
    except Exception:
        return False
    return exists(port)


def manual(port):
    """The one line to run by hand, for when the offer is declined or fails."""
    return (f'netsh advfirewall firewall add rule name={RULE} dir=in action=allow '
            f'protocol=TCP localport={port} profile=private')


def offer(store, port):
    """Ask once, act on the answer, and let the hub start either way.

    `store` is the hub's settings, used for one thing: remembering a no.
    """
    if not relevant():
        return None
    try:
        if store.get(DECLINED):
            return None
        if exists(port):
            return "already"
        print("\n  WINDOWS FIREWALL"
              "\n  " + "-" * 52 +
              "\n  Windows blocks other devices from reaching this laptop, so scout phones"
              "\n  cannot connect until it is allowed through. Nothing on this screen changes"
              "\n  when that is the problem - the phones simply time out.\n"
              "\n  It is one rule: this port, inbound, private networks only.")
        answer = input("\n  Add it now? Windows will ask you to confirm. [Y/n] ").strip().lower()
        if answer in ("n", "no"):
            store.set(DECLINED, True)
            print("\n  Not adding it. If phones cannot reach the hub, run this once in an"
                  "\n  Administrator Command Prompt:\n"
                  f"\n    {manual(port)}\n")
            return "declined"
        if add(port):
            print("\n  Allowed. Phones on the same wifi can reach this laptop.\n")
            return "added"
        # Refused at the UAC prompt, or no rights on a school-managed laptop.
        # Not remembered as a "no": next time may be a different account.
        print("\n  That did not go through. The hub is starting anyway. To do it by hand,"
              "\n  run this once in an Administrator Command Prompt:\n"
              f"\n    {manual(port)}\n")
        return "failed"
    except (EOFError, KeyboardInterrupt):
        print()
        return None
    except Exception as e:
        # A firewall helper must never be the reason a scouting hub did not start.
        sys.stderr.write(f"[firewall] {e}\n")
        return None
