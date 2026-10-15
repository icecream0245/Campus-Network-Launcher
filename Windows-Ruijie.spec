# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（PyInstaller 6.x）。

相比上游 spec 的改进：

* 入口改为顶层 ``main.py``，避免包内相对导入在打包后失效；
* 剔除用不到的重量级模块（numpy/pandas/setuptools 等）与 tcl 时区数据，
  同时保留 tkinter/tcl-tk 以支持可视化界面；
* 不使用 ``tinyWinToast``（上游的 git 依赖），Toast 由内置实现完成；
* 同时产出控制台版，便于用 ``--status`` 等方式排查问题。

打包命令：
    pyinstaller --noconfirm --clean Windows-Ruijie.spec
"""

APP_NAME = 'Ruijie_ePorta_Tool'
VERSION_FILE = 'version.txt'
ICON = 'ruijie_eporta/assets/wangluo.ico'

# 明确排除用不到的重量级/无关模块，减小体积
# 注意：不要排除 tkinter / _tkinter，界面需要它们
EXCLUDES = [
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
    hiddenimports=[
        # pystray 在运行期按平台动态选择后端，需要显式声明
        'pystray._win32',
        'PIL.Image',
        'PIL.ImageDraw',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
)

# 过滤掉 tcl 的时区数据库与旧版本 tcl 资源（界面用不到，可省下约 1 MB）
a.datas = [
    item
    for item in a.datas
    if 'tzdata' not in item[0].replace('/', '\\') and not item[0].replace('/', '\\').startswith('tcl8')
]

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
