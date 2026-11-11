"""Gera o arquivo de versão do Windows usado pelo PyInstaller (``--version-file``).

Aparece em Propriedades → Detalhes do .exe, e o FileDescription vira o nome do
processo no Gerenciador de Tarefas. Uso: ``python tools/make_version_file.py <saída>``.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vpnbridge import APP_NAME, AUTHOR, __version__  # noqa: E402

TEMPLATE = """\
VSVersionInfo(
  ffi=FixedFileInfo(filevers={nums}, prodvers={nums}, mask=0x3f, flags=0x0,
                    OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('041604B0', [
      StringStruct('CompanyName', {author!r}),
      StringStruct('FileDescription', {name!r}),
      StringStruct('FileVersion', {version!r}),
      StringStruct('InternalName', {name!r}),
      StringStruct('LegalCopyright', {copyright!r}),
      StringStruct('OriginalFilename', {exe!r}),
      StringStruct('ProductName', {name!r}),
      StringStruct('ProductVersion', {version!r})])]),
    VarFileInfo([VarStruct('Translation', [0x0416, 1200])])
  ]
)
"""


def render() -> str:
    parts = [int(p) for p in __version__.split(".")]
    nums = tuple((parts + [0, 0, 0, 0])[:4])
    return TEMPLATE.format(nums=nums, author=AUTHOR, name=APP_NAME, version=__version__,
                           copyright=f"Criado por {AUTHOR}", exe=f"{APP_NAME}.exe")


def main() -> None:
    out = Path(sys.argv[1])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(), encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
