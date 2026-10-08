"""Read-only terminal allowlist; never a shell, script runner, or OS-control escape."""

from app.computer.models import ComputerError


class CommandSafetyLayer:
    COMMANDS={"pwd":["/bin/pwd"],"date":["/bin/date"],"whoami":["/usr/bin/whoami"],"uname":["/usr/bin/uname","-a"],"sw_vers":["/usr/bin/sw_vers"]}
    def validate(self, arguments: list[str]) -> list[str]:
        if len(arguments)!=1 or arguments[0] not in self.COMMANDS:
            raise ComputerError("COMMAND_BLOCKED","Terminal access permits only fixed read-only system commands, never model-generated shell or scripts.")
        return self.COMMANDS[arguments[0]]
