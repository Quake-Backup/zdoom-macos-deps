#!/usr/bin/env python3

#
#    Helper module to build macOS version of various source ports
#    Copyright (C) 2020 Alexey Lysiuk
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
#

import sys

if sys.hexversion < 0x3070000:
    print('Build module requires Python 3.7 or newer')
    exit(1)


import argparse
import re
import os
import shutil
import subprocess
import tarfile


class Target(object):
    _SETUP_PREFIX = '_setup_'

    def __init__(self, name: str):
        self.name = name
        self.url = None
        self.cmake_options = {}
        self.post_build = None

    def setup(self, builder: 'Builder'):
        setup_func = getattr(Target, Target._SETUP_PREFIX + self.name)
        setup_func(self, builder)

    @staticmethod
    def get_target_names() -> list:
        targets = []

        for member in dir(Target):
            if member.startswith(Target._SETUP_PREFIX):
                targets.append(member[len(Target._SETUP_PREFIX):])

        return targets

    @staticmethod
    def create_targets() -> dict:
        return {name: Target(name) for name in Target.get_target_names()}

    def _setup_gzdoom(self, builder: 'Builder'):
        self.url = 'https://github.com/coelckers/gzdoom.git'
        self.post_build = Target._copy_moltenvk
        self._assign_zdoom_raze_cmake_options(builder)

    def _setup_qzdoom(self, builder: 'Builder'):
        self.url = 'https://github.com/madame-rachelle/qzdoom.git'
        self.post_build = Target._copy_moltenvk
        self._assign_zdoom_raze_cmake_options(builder)

    def _setup_lzdoom(self, builder: 'Builder'):
        self.url = 'https://github.com/drfrag666/gzdoom.git'
        self._assign_zdoom_raze_cmake_options(builder)

    def _setup_raze(self, builder: 'Builder'):
        self.url = 'https://github.com/coelckers/Raze.git'
        self._assign_zdoom_raze_cmake_options(builder)

    def _assign_zdoom_raze_cmake_options(self, builder: 'Builder'):
        extra_libs = (
            'mpg123',

            # FluidSynth with dependencies
            'fluidsynth',
            'instpatch-1.0',
            'glib-2.0',
            'gobject-2.0',
            'intl',
            'ffi',
            'pcre',

            # Sndfile with dependencies
            'sndfile',
            'ogg',
            'vorbis',
            'vorbisenc',
            'FLAC',
            'opus',
        )

        linker_args = '-framework AudioUnit -framework AudioToolbox -framework Carbon ' \
                      '-framework CoreAudio -framework CoreMIDI -framework CoreVideo -liconv'

        for lib in extra_libs:
            linker_args += f' {builder.lib_path}lib{lib}.a'

        opts = self.cmake_options
        opts['CMAKE_EXE_LINKER_FLAGS'] = linker_args
        opts['FORCE_INTERNAL_ZLIB'] = 'YES'
        opts['FORCE_INTERNAL_BZIP2'] = 'YES'
        opts['PK3_QUIET_ZIPDIR'] = 'YES'
        opts['DYN_OPENAL'] = 'NO'
        # Explicit OpenAL configuration to avoid selection of Apple's framework
        opts['OPENAL_INCLUDE_DIR'] = builder.include_path
        opts['OPENAL_LIBRARY'] = builder.lib_path + 'libopenal.a'

    @staticmethod
    def _copy_moltenvk(builder: 'Builder'):
        molten_lib = 'libMoltenVK.dylib'
        src_path = builder.lib_path + molten_lib
        dst_path = builder.build_path

        if builder.xcode:
            # TODO: Support other configurations
            dst_path += 'Debug' + os.sep

        dst_path += builder.target.name + '.app/Contents/MacOS' + os.sep
        os.makedirs(dst_path, exist_ok=True)

        dst_path += molten_lib

        if not os.path.exists(dst_path):
            copy_func = builder.xcode and os.symlink or shutil.copy
            copy_func(src_path, dst_path)


class Builder(object):
    def __init__(self, args: list):
        self.root_path = os.path.dirname(os.path.abspath(__file__)) + os.sep
        self.deps_path = self.root_path + 'deps' + os.sep
        self.prefix_path = self.root_path + 'prefix' + os.sep
        self.include_path = self.prefix_path + 'include' + os.sep
        self.lib_path = self.prefix_path + 'lib' + os.sep

        arguments = Builder._parse_arguments(args)

        self.xcode = arguments.xcode
        self.checkout_commit = arguments.checkout_commit
        self.generate = not arguments.skip_generate
        self.rebuild_prefix = arguments.rebuild_prefix
        self.build_path = arguments.build_path
        self.sdk_path = arguments.sdk_path
        self.create_package = arguments.create_package

        targets = Target.create_targets()

        if arguments.target:
            self.target = targets[arguments.target]
            self.source_path = self.root_path + 'source' + os.sep + self.target.name
        else:
            assert arguments.source_path
            self.source_path = arguments.source_path
            self._detect_target(targets)

        if not self.build_path:
            self.build_path = self.root_path + 'build' + os.sep + self.target.name + \
                os.sep + (self.xcode and 'xcode' or 'make')

        self.source_path += os.sep
        self.build_path += os.sep

        self.target.setup(self)

    def run(self):
        self._create_prefix_directory()
        self._prepare_source()
        self._generate_cmake()
        self._build_target()
        self._make_package()

    def _create_prefix_directory(self):
        if os.path.exists(self.prefix_path):
            if self.rebuild_prefix:
                shutil.rmtree(self.prefix_path)
            else:
                return

        os.makedirs(self.include_path)
        os.makedirs(self.lib_path)

        for dep in os.scandir(self.deps_path):
            if not dep.is_dir():
                continue

            dep_include_path = dep.path + os.sep + 'include' + os.sep
            dep_lib_path = dep.path + os.sep + 'lib' + os.sep

            if not os.path.exists(dep_include_path) or not os.path.exists(dep_lib_path):
                continue

            for dep_include in os.scandir(dep_include_path):
                os.symlink(dep_include.path, self.include_path + dep_include.name)

            for dep_lib in os.scandir(dep_lib_path):
                os.symlink(dep_lib.path, self.lib_path + dep_lib.name)

    def _prepare_source(self):
        if not os.path.exists(self.source_path):
            args = ('git', 'clone', self.target.url, self.source_path)
            subprocess.check_call(args, cwd=self.root_path)

        if self.checkout_commit:
            args = ['git', 'checkout', self.checkout_commit]
            subprocess.check_call(args, cwd=self.source_path)

    def _generate_cmake(self):
        if not self.generate:
            return

        environ = os.environ
        environ['PATH'] = environ['PATH'] + os.pathsep + '/Applications/CMake.app/Contents/bin'

        os.makedirs(self.build_path, exist_ok=True)

        args = [
            'cmake',
            self.xcode and '-GXcode' or '-GUnix Makefiles',
            '-DCMAKE_BUILD_TYPE=Release',
            '-DCMAKE_PREFIX_PATH=' + self.prefix_path,
        ]

        if self.sdk_path:
            args.append('-DCMAKE_OSX_SYSROOT=' + self.sdk_path)

        for cmake_arg_name, cmake_arg_value in self.target.cmake_options.items():
            args.append(f'-D{cmake_arg_name}={cmake_arg_value}')

        args.append(self.source_path)

        subprocess.check_call(args, cwd=self.build_path, env=environ)

    def _build_target(self):
        if self.xcode:
            # TODO: support case-sensitive file system
            args = ('open', self.target.name + '.xcodeproj')
        else:
            jobs = subprocess.check_output(['sysctl', '-n', 'hw.ncpu']).decode('ascii').strip()
            args = ('make', '-j', jobs)

        subprocess.check_call(args, cwd=self.build_path)

        if self.target.post_build:
            self.target.post_build(self)

    def _make_package(self):
        if not self.create_package or self.xcode:
            return

        args = ['git', 'describe', '--tags']
        version = subprocess.check_output(args, cwd=self.source_path).decode('ascii').strip()
        package_path = f'{self.build_path}{self.target.name}-{version}.tar.bz2'
        name_pos = len(self.build_path) - 1

        if os.path.exists(package_path):
            os.remove(package_path)

        def tar_filter(tarinfo):
            tarinfo.name = tarinfo.name[name_pos:]
            tarinfo.uname = tarinfo.gname = "root"
            tarinfo.uid = tarinfo.gid = 0
            return tarinfo

        with tarfile.open(package_path, 'w:bz2') as package:
            bundle_path = self.build_path + self.target.name + '.app'
            package.add(bundle_path, filter=tar_filter)

    def _detect_target(self, targets: dict):
        cmakelists_path = self.source_path + os.sep + 'CMakeLists.txt'
        project_name = None

        for line in open(cmakelists_path).readlines():
            match = re.search(r'project\s*\(\s*(\w+)\s*\)', line, re.IGNORECASE)
            if match:
                project_name = match.group(1).lower()
                break

        assert project_name
        self.target = targets[project_name]

    @staticmethod
    def _parse_arguments(args: list):
        parser = argparse.ArgumentParser(description='*ZDoom binary dependencies for macOS')

        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument('--target', choices=Target.get_target_names(), help='target to build')
        group.add_argument('--source-path', metavar='path', help='path to target\'s source code')

        group = parser.add_argument_group()
        group.add_argument('--xcode', action='store_true', help='generate Xcode project instead of build')
        group.add_argument('--checkout-commit', metavar='commit',
                           help='target\'s source code commit or tag to checkout')
        group.add_argument('--build-path', metavar='path', help='target build path')
        group.add_argument('--sdk-path', metavar='path', help='path to macOS SDK')
        group.add_argument('--skip-generate', action='store_true', help='do not generate build environment')
        group.add_argument('--create-package', action='store_true', help='create deployment package')
        group.add_argument('--rebuild-prefix', action='store_true', help='rebuild prefix path')

        return parser.parse_args(args)


if __name__ == '__main__':
    Builder(sys.argv[1:]).run()