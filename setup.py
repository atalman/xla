#!/usr/bin/env python
# Welcome to the PyTorch/XLA setup.py.
#
# Environment variables you are probably interested in:
#
#   DEBUG
#     build with -O0 and -g (debug symbols)
#
#   TORCH_XLA_VERSION
#     specify the version of PyTorch/XLA, rather than the hard-coded version
#     in this file; used when we're building binaries for distribution
#
#   VERSIONED_XLA_BUILD
#     creates a versioned build
#
#   TORCH_XLA_PACKAGE_NAME
#     change the package name to something other than 'torch_xla'
#
#   COMPILE_PARALLEL=1
#     enable parallel compile
#
#   BUILD_CPP_TESTS=1
#     build the C++ tests
#
#   XLA_DEBUG=0
#     build the xla/xrt client in debug mode
#
#   XLA_BAZEL_VERBOSE=0
#     turn on verbose messages during the bazel build of the xla/xrt client
#
#   XLA_CUDA=0
#     build the xla/xrt client with CUDA enabled
#

from __future__ import print_function

from setuptools import setup, find_packages, distutils
from torch.utils.cpp_extension import BuildExtension, CppExtension
import distutils.ccompiler
import distutils.command.clean
import glob
import inspect
import multiprocessing
import multiprocessing.pool
import os
import platform
import re
import shutil
import subprocess
import sys
import torch

base_dir = os.path.dirname(os.path.abspath(__file__))
third_party_path = os.path.join(base_dir, 'third_party')

_libtpu_version = '0.1.dev20220118'
_litbpu_storage_path = f'https://storage.googleapis.com/cloud-tpu-tpuvm-artifacts/wheels/libtpu-nightly/libtpu_nightly-{_libtpu_version}-py3-none-any.whl'


def _get_build_mode():
  for i in range(1, len(sys.argv)):
    if not sys.argv[i].startswith('-'):
      return sys.argv[i]


def _check_env_flag(name, default=''):
  return os.getenv(name, default).upper() in ['ON', '1', 'YES', 'TRUE', 'Y']


def get_git_head_sha(base_dir):
  xla_git_sha = subprocess.check_output(['git', 'rev-parse', 'HEAD'],
                                        cwd=base_dir).decode('ascii').strip()
  if os.path.isdir(os.path.join(base_dir, '..', '.git')):
    torch_git_sha = subprocess.check_output(['git', 'rev-parse', 'HEAD'],
                                            cwd=os.path.join(
                                                base_dir,
                                                '..')).decode('ascii').strip()
  else:
    torch_git_sha = ''
  return xla_git_sha, torch_git_sha


def get_build_version(xla_git_sha):
  version = os.getenv('TORCH_XLA_VERSION', '1.11')
  if _check_env_flag('VERSIONED_XLA_BUILD', default='0'):
    try:
      version += '+' + xla_git_sha[:7]
    except Exception:
      pass
  return version


def create_version_files(base_dir, version, xla_git_sha, torch_git_sha):
  print('Building torch_xla version: {}'.format(version))
  print('XLA Commit ID: {}'.format(xla_git_sha))
  print('PyTorch Commit ID: {}'.format(torch_git_sha))
  py_version_path = os.path.join(base_dir, 'torch_xla', 'version.py')
  with open(py_version_path, 'w') as f:
    f.write('# Autogenerated file, do not edit!\n')
    f.write("__version__ = '{}'\n".format(version))
    f.write("__xla_gitrev__ = '{}'\n".format(xla_git_sha))
    f.write("__torch_gitrev__ = '{}'\n".format(torch_git_sha))

  cpp_version_path = os.path.join(base_dir, 'torch_xla', 'csrc', 'version.cpp')
  with open(cpp_version_path, 'w') as f:
    f.write('// Autogenerated file, do not edit!\n')
    f.write('#include "torch_xla/csrc/version.h"\n\n')
    f.write('namespace torch_xla {\n\n')
    f.write('const char XLA_GITREV[] = {{"{}"}};\n'.format(xla_git_sha))
    f.write('const char TORCH_GITREV[] = {{"{}"}};\n\n'.format(torch_git_sha))
    f.write('}  // namespace torch_xla\n')


def generate_xla_aten_code(base_dir):
  generate_code_cmd = [os.path.join(base_dir, 'scripts', 'generate_code.sh')]
  if subprocess.call(generate_code_cmd) != 0:
    print(
        'Failed to generate ATEN bindings: {}'.format(generate_code_cmd),
        file=sys.stderr)
    sys.exit(1)


def build_extra_libraries(base_dir, build_mode=None):
  build_libs_cmd = [os.path.join(base_dir, 'build_torch_xla_libs.sh')]
  cxx_abi = getattr(torch._C, '_GLIBCXX_USE_CXX11_ABI', None)
  if cxx_abi is not None:
    build_libs_cmd += ['-O', '-D_GLIBCXX_USE_CXX11_ABI={}'.format(int(cxx_abi))]
  if build_mode is not None:
    build_libs_cmd += [build_mode]
  if subprocess.call(build_libs_cmd) != 0:
    print(
        'Failed to build external libraries: {}'.format(build_libs_cmd),
        file=sys.stderr)
    sys.exit(1)


def generate_protos(base_dir, third_party_path):
  # Application proto files should be in torch_xla/pb/src/ and the generated
  # files will go in torch_xla/pb/cpp/.
  proto_files = glob.glob(os.path.join(base_dir, 'torch_xla/pb/src/*.proto'))
  if proto_files:
    protoc = os.path.join(
        third_party_path,
        'tensorflow/bazel-out/host/bin/external/com_google_protobuf/protoc')
    protoc_cmd = [
        protoc, '-I',
        os.path.join(third_party_path, 'tensorflow'), '-I',
        os.path.join(base_dir, 'torch_xla/pb/src'), '--cpp_out',
        os.path.join(base_dir, 'torch_xla/pb/cpp')
    ] + proto_files
    if subprocess.call(protoc_cmd) != 0:
      print(
          'Failed to generate protobuf files: {}'.format(protoc_cmd),
          file=sys.stderr)
      sys.exit(1)


def _compile_parallel(self,
                      sources,
                      output_dir=None,
                      macros=None,
                      include_dirs=None,
                      debug=0,
                      extra_preargs=None,
                      extra_postargs=None,
                      depends=None):
  # Those lines are copied from distutils.ccompiler.CCompiler directly.
  macros, objects, extra_postargs, pp_opts, build = self._setup_compile(
      output_dir, macros, include_dirs, sources, depends, extra_postargs)
  cc_args = self._get_cc_args(pp_opts, debug, extra_preargs)

  def compile_one(obj):
    try:
      src, ext = build[obj]
    except KeyError:
      return
    self._compile(obj, src, ext, cc_args, extra_postargs, pp_opts)

  list(
      multiprocessing.pool.ThreadPool(multiprocessing.cpu_count()).imap(
          compile_one, objects))
  return objects


# Plant the parallel compile function.
if _check_env_flag('COMPILE_PARALLEL', default='1'):
  try:
    if (inspect.signature(distutils.ccompiler.CCompiler.compile) ==
        inspect.signature(_compile_parallel)):
      distutils.ccompiler.CCompiler.compile = _compile_parallel
  except:
    pass


class Clean(distutils.command.clean.clean):

  def run(self):
    import glob
    import re
    with open('.gitignore', 'r') as f:
      ignores = f.read()
      pat = re.compile(r'^#( BEGIN NOT-CLEAN-FILES )?')
      for wildcard in filter(None, ignores.split('\n')):
        match = pat.match(wildcard)
        if match:
          if match.group(1):
            # Marker is found and stop reading .gitignore.
            break
          # Ignore lines which begin with '#'.
        else:
          for filename in glob.glob(wildcard):
            try:
              os.remove(filename)
            except OSError:
              shutil.rmtree(filename, ignore_errors=True)

    # It's an old-style class in Python 2.7...
    distutils.command.clean.clean.run(self)


class Build(BuildExtension):

  def run(self):
    # Run the original BuildExtension first. We need this before building
    # the tests.
    BuildExtension.run(self)
    if _check_env_flag('BUILD_CPP_TESTS', default='1'):
      # Build the C++ tests.
      cmd = [os.path.join(base_dir, 'test/cpp/run_tests.sh'), '-B']
      if subprocess.call(cmd) != 0:
        print('Failed to build tests: {}'.format(cmd), file=sys.stderr)
        sys.exit(1)


xla_git_sha, torch_git_sha = get_git_head_sha(base_dir)
version = get_build_version(xla_git_sha)

build_mode = _get_build_mode()
if build_mode not in ['clean']:
  # Generate version info (torch_xla.__version__).
  create_version_files(base_dir, version, xla_git_sha, torch_git_sha)

  # Generate the code before globbing!
  generate_xla_aten_code(base_dir)

  # Build the support libraries (ie, TF).
  build_extra_libraries(base_dir, build_mode=build_mode)

  # Generate the proto C++/python files only after third_party has built.
  generate_protos(base_dir, third_party_path)

# Fetch the sources to be built.
torch_xla_sources = (
    glob.glob('torch_xla/csrc/*.cpp') + glob.glob('torch_xla/csrc/ops/*.cpp') +
    glob.glob('torch_xla/pb/cpp/*.cc'))

# Constant known variables used throughout this file.
lib_path = os.path.join(base_dir, 'torch_xla/lib')
pytorch_source_path = os.getenv('PYTORCH_SOURCE_PATH',
                                os.path.dirname(base_dir))

# Setup include directories folders.
include_dirs = [
    base_dir,
]
for ipath in [
    'tensorflow/bazel-tensorflow',
    'tensorflow/bazel-bin',
    'tensorflow/bazel-tensorflow/external/protobuf_archive/src',
    'tensorflow/bazel-tensorflow/external/com_google_protobuf/src',
    'tensorflow/bazel-tensorflow/external/eigen_archive',
    'tensorflow/bazel-tensorflow/external/com_google_absl',
]:
  include_dirs.append(os.path.join(third_party_path, ipath))
include_dirs += [
    pytorch_source_path,
    os.path.join(pytorch_source_path, 'torch/csrc'),
    os.path.join(pytorch_source_path, 'torch/lib/tmp_install/include'),
]

library_dirs = []
library_dirs.append(lib_path)

extra_link_args = []

DEBUG = _check_env_flag('DEBUG')
IS_DARWIN = (platform.system() == 'Darwin')
IS_LINUX = (platform.system() == 'Linux')


def make_relative_rpath(path):
  if IS_DARWIN:
    return '-Wl,-rpath,@loader_path/' + path
  else:
    return '-Wl,-rpath,$ORIGIN/' + path


extra_compile_args = [
    '-std=c++14',
    '-Wno-sign-compare',
    '-Wno-deprecated-declarations',
    '-Wno-return-type',
]

if re.match(r'clang', os.getenv('CC', '')):
  extra_compile_args += [
      '-Wno-macro-redefined',
      '-Wno-return-std-move',
  ]

if DEBUG:
  extra_compile_args += ['-O0', '-g']
  extra_link_args += ['-O0', '-g']
else:
  extra_compile_args += ['-DNDEBUG']

extra_link_args += ['-lxla_computation_client']

setup(
    name=os.environ.get('TORCH_XLA_PACKAGE_NAME', 'torch_xla'),
    version=version,
    description='XLA bridge for PyTorch',
    url='https://github.com/pytorch/xla',
    author='PyTorch/XLA Dev Team',
    author_email='pytorch-xla@googlegroups.com',
    # Exclude the build files.
    packages=find_packages(exclude=['build']),
    ext_modules=[
        CppExtension(
            '_XLAC',
            torch_xla_sources,
            include_dirs=include_dirs,
            extra_compile_args=extra_compile_args,
            library_dirs=library_dirs,
            extra_link_args=extra_link_args + \
                [make_relative_rpath('torch_xla/lib')],
        ),
    ],
    extras_require={
        # On Cloud TPU VM install with:
        # $ sudo pip3 install torch_xla[tpuvm] -f https://storage.googleapis.com/tpu-pytorch/wheels/tpuvm/torch_xla-1.10-cp38-cp38-linux_x86_64.whl
        'tpuvm': [f'libtpu-nightly @ {_litbpu_storage_path}'],
    },
    package_data={
        'torch_xla': [
            'lib/*.so*',
        ],
    },
    data_files=[
        'test/cpp/build/test_ptxla',
        'scripts/fixup_binary.py',
    ],
    cmdclass={
        'build_ext': Build,
        'clean': Clean,
    })
