# -*- coding: utf-8 -*-
#-----------------------------------------------------------------------------
# (C) British Crown Copyright 2012-3 Met Office.
#
# This file is part of Rose, a framework for scientific suites.
#
# Rose is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Rose is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Rose. If not, see <http://www.gnu.org/licenses/>.
#-----------------------------------------------------------------------------
"""Builtin application: rose_prune: suite housekeeping application."""

from glob import glob
import os
from rose.app_run import BuiltinApp, ConfigValueError
from rose.date import RoseDateShifter, OffsetValueError
from rose.env import env_var_process, UnboundEnvironmentVariableError
from rose.fs_util import FileSystemEvent
from rose.popen import RosePopenError
from rose.suite_log_view import SuiteLogViewGenerator
import shlex

class RosePruneApp(BuiltinApp):

    """Prune files and directories generated by suite tasks."""

    SCHEME = "rose_prune"
    SECTION = "prune"

    def run(self, app_runner, config, opts, args, uuid, work_files):
        """Suite housekeeping application.

        This application is designed to work under "rose task-run" in a cycling
        suite.

        """
        suite_name = os.getenv("ROSE_SUITE_NAME")
        if not suite_name:
            return
        prune_remote_logs_cycles = self._get_conf(config,
                                                  "prune-remote-logs-at")
        archive_logs_cycles = self._get_conf(config, "archive-logs-at")
        if prune_remote_logs_cycles or archive_logs_cycles:
            slvg = SuiteLogViewGenerator(
                    event_handler=app_runner.event_handler,
                    fs_util=app_runner.fs_util,
                    popen=app_runner.popen,
                    suite_engine_proc=app_runner.suite_engine_proc)
            prune_remote_logs_cycles = filter(
                    lambda c: c not in archive_logs_cycles,
                    prune_remote_logs_cycles)
            if prune_remote_logs_cycles:
                slvg.generate(suite_name, prune_remote_logs_cycles,
                              prune_remote_mode=True)
            if archive_logs_cycles:
                slvg.generate(suite_name, archive_logs_cycles,
                              archive_mode=True)
        globs = []
        suite_engine_proc = app_runner.suite_engine_proc
        for key in ["datac", "work"]:
            k = "prune-" + key + "-at"
            for cycle, cycle_args_str in self._get_conf(config, k,
                                                        arg_ok=True):
                head = suite_engine_proc.get_cycle_items_globs(key, cycle)
                if cycle_args_str:
                    for cycle_arg in shlex.split(cycle_args_str):
                        globs.append(os.path.join(head, cycle_arg))
                else:
                    globs.append(head)
        hosts = suite_engine_proc.get_suite_jobs_auths(suite_name)
        suite_dir_rel = suite_engine_proc.get_suite_dir_rel(suite_name)
        sh_cmd_args = {"d": suite_dir_rel, "g": " ".join(globs)}
        sh_cmd = ((r"set -e; " +
                   r"cd %(d)s; " +
                   r"(ls -d %(g)s 2>/dev/null || true) | sort; " +
                   r"rm -rf %(g)s") % sh_cmd_args)
        for host in hosts:
            cmd = app_runner.popen.get_cmd("ssh", host, sh_cmd)
            try:
                out, err = app_runner.popen.run_ok(*cmd)
            except RosePopenError as e:
                app_runner.handle_event(e)
                print e
            else:
                event = FileSystemEvent(FileSystemEvent.CHDIR,
                                        host + ":" + suite_dir_rel)
                app_runner.handle_event(event)
                for line in out.splitlines():
                    event = FileSystemEvent(FileSystemEvent.DELETE,
                                            host + ":" + line)
                    app_runner.handle_event(event)
        cwd = os.getcwd()
        app_runner.fs_util.chdir(suite_engine_proc.get_suite_dir(suite_name))
        try:
            for g in globs:
                for name in sorted(glob(g)):
                    app_runner.fs_util.delete(name)
        finally:
            app_runner.fs_util.chdir(cwd)
        return

    def _get_conf(self, config, key, arg_ok=False):
        """Get a list of cycles from a configuration setting.

        key -- An option key in self.SECTION to locate the setting.
        arg_ok -- A boolean to indicate whether an item in the list can have
                  extra arguments or not.

        The value of the setting is expected to be split by shlex.split into a
        list of items. If arg_ok is False, an item should be a string
        representing a cycle or an cycle offset. If arg_ok is True, the cycle
        or cycle offset string can, optionally, have an argument after a colon.
        E.g.:

        prune-remote-logs-at=-6h -12h
        prune-datac-at=-6h:foo/* -12h:'bar/* baz/*' -1d

        If arg_ok is False, return a list of cycles.
        If arg_ok is True, return a list of (cycle, arg)

        """
        items_str = config.get_value([self.SECTION, key])
        if items_str is None:
            return []
        try:
            items_str = env_var_process(items_str)
        except UnboundEnvironmentVariableError as e:
            raise ConfigValueError([self.SECTION, key], items_str, e)
        items = []
        ds = RoseDateShifter(task_cycle_time_mode=True)
        for item_str in shlex.split(items_str):
            if arg_ok and ":" in item_str:
                item, arg = item_str.split(":", 1)
            else:
                item, arg = (item_str, None)
            if ds.is_task_cycle_time_mode() and ds.is_offset(item):
                cycle = ds.date_shift(offset=item)
            else:
                cycle = item
            if arg_ok:
                items.append((cycle, arg))
            else:
                items.append(cycle)
        return items
