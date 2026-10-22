import subprocess

SERVICE_NAMES = ("old", "new")
FIXED_RELEASES = {
    (8, 2): (8, 2, 35),
    (8, 3): (8, 3, 36),
    (8, 4): (8, 4, 27),
    (8, 5): (8, 5, 12),
}

PHP_PROBE = r'''
echo "version=" . PHP_VERSION . PHP_EOL;
$names = [
    "session.upload_progress.enabled",
    "memory_limit",
    "post_max_size",
    "fatal_error_backtraces",
];
foreach ($names as $name) {
    $value = ini_get($name);
    if ($value === false) {
        $value = "NOT_AVAILABLE";
    }
    echo $name . "=" . $value . PHP_EOL;
}
'''

def run_in_service(service, *command):
    completed = subprocess.run(
        ["docker", "compose", "exec", "-T", service, *command],
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout, completed.stderr


def read_php_details(service):
    stdout, _ = run_in_service(service, "php", "-r", PHP_PROBE)
    details = {}
    for line in stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            details[key] = value
    return details


def detect_runtime(service):
    stdout, stderr = run_in_service(service, "apache2ctl", "-M")
    modules = stdout + stderr
    if "php_module" in modules:
        return "mod_php"
    return "unknown"


def parse_version(version_text):
    core_version = version_text.split("-", 1)[0]
    return tuple(int(part) for part in core_version.split(".")[:3])


def format_version(version_tuple):
    return ".".join(str(part) for part in version_tuple)


def is_disabled(value):
    return value.strip().lower() in {"", "0", "off", "false", "no"}


def parse_size(value):
    normalized = value.strip().upper()
    if normalized == "-1":
        return -1
    multipliers = {
        "K": 1024,
        "M": 1024 * 1024,
        "G": 1024 * 1024 * 1024,
    }
    if normalized[-1] in multipliers:
        return int(normalized[:-1]) * multipliers[normalized[-1]]
    return int(normalized)


def has_partial_mitigations(details, branch):
    session_disabled = is_disabled(
        details["session.upload_progress.enabled"]
    )
    memory_unlimited = details["memory_limit"].strip() == "-1"
    post_size = parse_size(details["post_max_size"])
    post_size_conservative = 0 < post_size <= 8 * 1024 * 1024

    fatal_backtraces_disabled = True
    if branch == (8, 5):
        fatal_backtraces_disabled = is_disabled(
            details["fatal_error_backtraces"]
        )

    return all(
        (
            session_disabled,
            memory_unlimited,
            post_size_conservative,
            fatal_backtraces_disabled,
        )
    )


def assess(details):
    version_text = details["version"]
    version_tuple = parse_version(version_text)
    branch = version_tuple[:2]

    if branch <= (8, 1):
        return (
            "NOT PATCHED",
            f"PHP {version_text} is on a branch that no longer receives "
            "updates from the PHP project.",
        )

    fixed_release = FIXED_RELEASES.get(branch)
    if fixed_release is None:
        return (
            "NOT PATCHED",
            f"PHP {version_text} is outside this advisory's verified "
            "8.2 through 8.5 release matrix.",
        )

    if version_tuple >= fixed_release:
        return (
            "PATCHED",
            f"PHP {version_text} meets the fixed release "
            f"{format_version(fixed_release)} for its branch.",
        )

    if has_partial_mitigations(details, branch):
        return (
            "NOT PATCHED, PARTIAL MITIGATIONS PRESENT",
            f"PHP {version_text} is below fixed release "
            f"{format_version(fixed_release)}; the lab stopgaps are present, "
            "but PHP says they do not make the installation safe.",
        )

    return (
        "NOT PATCHED",
        f"PHP {version_text} is below fixed release "
        f"{format_version(fixed_release)} and the full lab stopgap set "
        "is not present.",
    )


def inspect_service(service):
    details = read_php_details(service)
    status, reason = assess(details)
    return {
        "service": service,
        "php_version": details["version"],
        "web_runtime": detect_runtime(service),
        "session.upload_progress.enabled": details[
            "session.upload_progress.enabled"
        ],
        "memory_limit": details["memory_limit"],
        "post_max_size": details["post_max_size"],
        "fatal_error_backtraces": details["fatal_error_backtraces"],
        "status": status,
        "reason": reason,
    }


def collect_reports():
    return [inspect_service(service) for service in SERVICE_NAMES]


def print_reports(reports):
    for report in reports:
        print(f"\n=== {report['service'].upper()} ===")
        print(f"PHP version: {report['php_version']}")
        print(f"Web runtime: {report['web_runtime']}")
        print(
            "session.upload_progress.enabled: "
            f"{report['session.upload_progress.enabled']}"
        )
        print(f"memory_limit: {report['memory_limit']}")
        print(f"post_max_size: {report['post_max_size']}")
        print(
            "fatal_error_backtraces: "
            f"{report['fatal_error_backtraces']}"
        )
        print(f"Status: {report['status']}")
        print(f"Reason: {report['reason']}")


if __name__ == "__main__":
    try:
        print_reports(collect_reports())
    except subprocess.CalledProcessError as error:
        print("The checker could not inspect the running lab.")
        print("Confirm Docker Desktop is running and both services are up.")
        if error.stderr:
            print(error.stderr.strip())