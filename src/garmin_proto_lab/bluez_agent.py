"""BlueZ Agent1 support for interactive headless pairing on Linux."""
from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Callable


class BluezAgentUnavailable(RuntimeError):
    pass


PasskeyPrompt = Callable[[str], int]
ConfirmationPrompt = Callable[[str, int], bool]
AuthorizationPrompt = Callable[[str], bool]


def _default_passkey_prompt(device: str) -> int:
    text = input(f"Bluetooth passkey shown by {device}: ").strip()
    if not text.isdigit():
        raise ValueError("Bluetooth passkey must be decimal digits")
    value = int(text)
    if not 0 <= value <= 999_999:
        raise ValueError("Bluetooth passkey must be between 000000 and 999999")
    return value


def _default_confirmation_prompt(device: str, passkey: int) -> bool:
    answer = input(f"Confirm Bluetooth passkey {passkey:06d} for {device}? [y/N] ").strip().lower()
    return answer in {"y", "yes"}


def _default_authorization_prompt(device: str) -> bool:
    answer = input(f"Authorize Bluetooth pairing for {device}? [y/N] ").strip().lower()
    return answer in {"y", "yes"}


@dataclass(slots=True)
class BluezPairingAgent:
    """Register an interactive KeyboardDisplay agent for one pairing operation."""

    passkey_prompt: PasskeyPrompt = _default_passkey_prompt
    confirmation_prompt: ConfirmationPrompt = _default_confirmation_prompt
    authorization_prompt: AuthorizationPrompt = _default_authorization_prompt
    path: str = "/org/openai/garmin_proto_lab/agent"
    _bus: object | None = None
    _manager: object | None = None
    _interface: object | None = None
    _registered: bool = False

    async def __aenter__(self) -> "BluezPairingAgent":
        if not sys.platform.startswith("linux"):
            return self
        try:
            from dbus_fast.aio import MessageBus
            from dbus_fast.constants import BusType
            from dbus_fast.service import ServiceInterface, method
        except ImportError as exc:
            raise BluezAgentUnavailable("dbus-fast is required for Linux BlueZ pairing") from exc

        outer = self

        class Agent(ServiceInterface):
            def __init__(self) -> None:
                super().__init__("org.bluez.Agent1")

            @method()
            def Release(self):
                return None

            @method()
            def RequestPinCode(self, device: "o") -> "s":
                return f"{outer.passkey_prompt(str(device)):06d}"

            @method()
            def DisplayPinCode(self, device: "o", pincode: "s"):
                print(f"Bluetooth PIN for {device}: {pincode}")

            @method()
            def RequestPasskey(self, device: "o") -> "u":
                return outer.passkey_prompt(str(device))

            @method()
            def DisplayPasskey(self, device: "o", passkey: "u", entered: "q"):
                print(f"Bluetooth passkey for {device}: {int(passkey):06d} ({int(entered)} entered)")

            @method()
            def RequestConfirmation(self, device: "o", passkey: "u"):
                if not outer.confirmation_prompt(str(device), int(passkey)):
                    from dbus_fast.errors import DBusError
                    raise DBusError("org.bluez.Error.Rejected", "Bluetooth pairing was rejected")

            @method()
            def RequestAuthorization(self, device: "o"):
                if not outer.authorization_prompt(str(device)):
                    from dbus_fast.errors import DBusError
                    raise DBusError("org.bluez.Error.Rejected", "Bluetooth authorization was rejected")

            @method()
            def AuthorizeService(self, device: "o", uuid: "s"):
                return None

            @method()
            def Cancel(self):
                return None

        bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        agent = Agent()
        bus.export(self.path, agent)
        introspection = await bus.introspect("org.bluez", "/org/bluez")
        proxy = bus.get_proxy_object("org.bluez", "/org/bluez", introspection)
        manager = proxy.get_interface("org.bluez.AgentManager1")
        await manager.call_register_agent(self.path, "KeyboardDisplay")
        await manager.call_request_default_agent(self.path)
        self._bus = bus
        self._manager = manager
        self._interface = agent
        self._registered = True
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        manager = self._manager
        if self._registered and manager is not None:
            try:
                await manager.call_unregister_agent(self.path)
            except Exception:
                pass
        bus = self._bus
        if bus is not None:
            disconnect = getattr(bus, "disconnect", None)
            if disconnect is not None:
                result = disconnect()
                if hasattr(result, "__await__"):
                    await result
        self._registered = False
        self._manager = None
        self._bus = None
        self._interface = None
