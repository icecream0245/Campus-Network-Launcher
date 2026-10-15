# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（PyInstaller 6.x）。

相比上游 spec 的改进：

* 入口改为顶层 ``main.py``，避免包内相对导入在打包后失效；
* 移除 tkinter / tk / tcl 及其数据文件（通知与弹窗改用 WinRT Toast 和
  ``ctypes.MessageBoxW``），可执行文件体积显著下降；
* 不使用 ``tinyWinToast``（上游的 git 依赖），Toast 由内置实现完成；
* 同时产出控制台版，便于用 ``--status`` 等方式排查问题。

打包命令：
    pyinstaller --noconfirm --clean Windows-Ruijie.spec
"""

APP_NAME = 'Ruijie_ePorta_Tool'
VERSION_FILE = 'version.txt'
ICON = 'ruijie_eporta/assets/wangluo.ico'

# 明确排除用不到的重量级/无关模块，减小体积
EXCLUDES = [
    'tkinter',
    '_tkinter',
    'turtle',
    'turtledemo',
    'idlelib',
    'pydoc_data',
    'lib2to3',
    'distutils',
    'test',
    'unittest',
    'doctest',
    'pdb',
    'bdb',
    'difflib',
    'multiprocessing',
    'numpy',
    'pandas',
    'matplotlib',
    'scipy',
    'PIL',
    'setuptools',
    'pip',
    'wheel',
    'pytest',
    'sqlite3',
]

a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=[],
    datas=[(ICON, 'ruijie_eporta/assets')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe_gui = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    # 若运行时报 "Could not create temporary directory!"，把这行改成一个确定可写的绝对路径，
    # 例如：runtime_tmpdir=r'D:\RuijieEporta\tmp'
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=VERSION_FILE,
    icon=ICON,
)

exe_console = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=APP_NAME + '_Console',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=VERSION_FILE,
    icon=ICON,
)
