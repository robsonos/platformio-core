# Copyright 2014-present Ivan Kravets <me@ikravets.com>
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import json
import os
from os import getenv
from os.path import isdir, join
from time import time

import click
import semantic_version

from platformio import __version__, app, exception, telemetry, util
from platformio.commands.lib import lib_update as cmd_libraries_update
from platformio.commands.platform import \
    platform_install as cmd_platform_install
from platformio.commands.platform import platform_update as cmd_platform_update
from platformio.commands.upgrade import get_latest_version
from platformio.libmanager import LibraryManager
from platformio.managers.platform import PlatformManager


def in_silence(ctx):
    ctx_args = ctx.args or []
    return (ctx_args and
            (ctx.args[0] == "upgrade" or "--json-output" in ctx_args))


def on_platformio_start(ctx, force, caller):
    app.set_session_var("command_ctx", ctx)
    app.set_session_var("force_option", force)
    app.set_session_var("caller_id", caller)
    telemetry.on_command()

    if not in_silence(ctx):
        after_upgrade(ctx)


def on_platformio_end(ctx, result):  # pylint: disable=W0613
    if in_silence(ctx):
        return

    try:
        check_platformio_upgrade()
        check_internal_updates(ctx, "platforms")
        check_internal_updates(ctx, "libraries")
    except (exception.GetLatestVersionError, exception.APIRequestError):
        click.secho("Failed to check for PlatformIO upgrades. "
                    "Please check your Internet connection.", fg="red")


def on_platformio_exception(e):
    telemetry.on_exception(e)


class Upgrader(object):

    def __init__(self, from_version, to_version):
        self.from_version = semantic_version.Version.coerce(from_version)
        self.to_version = semantic_version.Version.coerce(to_version)

        self._upgraders = [
            (semantic_version.Version("3.0.0"), self._upgrade_to_3_0_0)
        ]

    def run(self, ctx):
        if self.from_version > self.to_version:
            return True

        result = [True]
        for item in self._upgraders:
            if self.from_version >= item[0] or self.to_version < item[0]:
                continue
            result.append(item[1](ctx))

        return all(result)

    def _upgrade_to_3_0_0(self, ctx):  # pylint: disable=R0201
        # convert custom board configuration
        boards_dir = join(util.get_home_dir(), "boards")
        if isdir(boards_dir):
            for item in os.listdir(boards_dir):
                if not item.endswith(".json"):
                    continue
                data = util.load_json(join(boards_dir, item))
                if set(["name", "url", "vendor"]) <= set(data.keys()):
                    continue
                os.remove(join(boards_dir, item))
                for key, value in data.items():
                    with open(join(boards_dir, "%s.json" % key),
                              "w") as f:
                        json.dump(value, f, sort_keys=True, indent=2)

        # re-install PlatformIO 2.0 development platforms
        installed_platforms = app.get_state_item("installed_platforms", [])
        if installed_platforms:
            ctx.invoke(cmd_platform_install, platforms=installed_platforms)

        return True


def after_upgrade(ctx):
    last_version = app.get_state_item("last_version", "0.0.0")
    if last_version == __version__:
        return

    if last_version == "0.0.0":
        app.set_state_item("last_version", __version__)
    else:
        click.secho("Please wait while upgrading PlatformIO ...",
                    fg="yellow")

        u = Upgrader(last_version, __version__)
        if u.run(ctx):
            app.set_state_item("last_version", __version__)

            # patch development platforms
            pm = PlatformManager()
            for manifest in pm.get_installed():
                pm.update(manifest['name'], "~" + manifest['version'])

            click.secho("PlatformIO has been successfully upgraded to %s!\n" %
                        __version__, fg="green")

            telemetry.on_event(category="Auto", action="Upgrade",
                               label="%s > %s" % (last_version, __version__))
        else:
            raise exception.UpgradeError("Auto upgrading...")
        click.echo("")

    # PlatformIO banner
    terminal_width, _ = click.get_terminal_size()
    click.echo("*" * terminal_width)
    click.echo("If you like %s, please:" % (
        click.style("PlatformIO", fg="cyan")
    ))
    click.echo(
        "- %s us on Twitter to stay up-to-date "
        "on the latest project news > %s" %
        (click.style("follow", fg="cyan"),
         click.style("https://twitter.com/PlatformIO_Org", fg="cyan"))
    )
    click.echo("- %s it on GitHub > %s" % (
        click.style("star", fg="cyan"),
        click.style("https://github.com/platformio/platformio", fg="cyan")
    ))
    if not getenv("PLATFORMIO_IDE"):
        click.echo("- %s PlatformIO IDE for IoT development > %s" % (
            click.style("try", fg="cyan"),
            click.style("http://platformio.org/platformio-ide", fg="cyan")
        ))
    if not util.is_ci():
        click.echo("- %s to keep PlatformIO alive! > %s" % (
            click.style("donate", fg="cyan"),
            click.style("http://platformio.org/donate", fg="cyan")
        ))

    click.echo("*" * terminal_width)
    click.echo("")


def check_platformio_upgrade():
    last_check = app.get_state_item("last_check", {})
    interval = int(app.get_setting("check_platformio_interval")) * 3600 * 24
    if (time() - interval) < last_check.get("platformio_upgrade", 0):
        return

    last_check['platformio_upgrade'] = int(time())
    app.set_state_item("last_check", last_check)

    latest_version = get_latest_version()
    if (semantic_version.Version.coerce(latest_version) <=
            semantic_version.Version.coerce(__version__)):
        return

    terminal_width, _ = click.get_terminal_size()

    click.echo("")
    click.echo("*" * terminal_width)
    click.secho("There is a new version %s of PlatformIO available.\n"
                "Please upgrade it via `" % latest_version,
                fg="yellow", nl=False)
    if getenv("PLATFORMIO_IDE"):
        click.secho("PlatformIO IDE Menu: Upgrade PlatformIO",
                    fg="cyan", nl=False)
        click.secho("`.", fg="yellow")
    else:
        click.secho("platformio upgrade", fg="cyan", nl=False)
        click.secho("` or `", fg="yellow", nl=False)
        click.secho("pip install -U platformio", fg="cyan", nl=False)
        click.secho("` command.", fg="yellow")
    click.secho("Changes: ", fg="yellow", nl=False)
    click.secho("http://docs.platformio.org/en/latest/history.html",
                fg="cyan")
    click.echo("*" * terminal_width)
    click.echo("")


def check_internal_updates(ctx, what):
    last_check = app.get_state_item("last_check", {})
    interval = int(app.get_setting("check_%s_interval" % what)) * 3600 * 24
    if (time() - interval) < last_check.get(what + "_update", 0):
        return

    last_check[what + '_update'] = int(time())
    app.set_state_item("last_check", last_check)

    outdated_items = []
    if what == "platforms":
        pm = PlatformManager()
        for manifest in pm.get_installed():
            if pm.is_outdated(manifest['name'], manifest['version']):
                outdated_items.append(
                    "%s@%s" % (manifest['name'], manifest['version']))
    elif what == "libraries":
        lm = LibraryManager()
        outdated_items = lm.get_outdated()

    if not outdated_items:
        return

    terminal_width, _ = click.get_terminal_size()

    click.echo("")
    click.echo("*" * terminal_width)
    click.secho("There are the new updates for %s (%s)" %
                (what, ", ".join(outdated_items)), fg="yellow")

    if not app.get_setting("auto_update_" + what):
        click.secho("Please update them via ", fg="yellow", nl=False)
        click.secho("`platformio %s update`" %
                    ("lib" if what == "libraries" else "platform"),
                    fg="cyan", nl=False)
        click.secho(" command.", fg="yellow")
    else:
        click.secho("Please wait while updating %s ..." % what, fg="yellow")
        if what == "platforms":
            ctx.invoke(cmd_platform_update)
        elif what == "libraries":
            ctx.invoke(cmd_libraries_update)
        click.echo()

        telemetry.on_event(category="Auto", action="Update",
                           label=what.title())

    click.echo("*" * terminal_width)
    click.echo("")
