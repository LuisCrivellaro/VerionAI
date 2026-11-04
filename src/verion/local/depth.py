"""Estimadores de profundidade para o método ``parallax-2.5d`` (spec 3.6).

Contrato do mapa de profundidade, igual para qualquer estimador:

- PNG em tons de cinza de 16 bits (modo ``I;16`` do Pillow);
- mesma resolução da foto **já orientada** (depois de aplicar a orientação EXIF), isto é, o que o
  espectador vê. Um estimador que rode sobre os pixels crus de uma foto com EXIF de rotação precisa
  girar o mapa antes de entregá-lo; o parallax recusa tamanho diferente em vez de reamostrar às cegas;
- **branco = perto, preto = longe** (convenção de disparidade/profundidade inversa, a mesma dos
  modelos de profundidade monocular relativa). Só a ordem importa: a escala é normalizada pelo parallax.

O padrão do harness é ``DepthFromFile``: lê um mapa que já está em disco. Isso mantém a Fase 0 sem GPU,
sem PyTorch e sem custo. Um estimador real (modelo de profundidade) entra depois como outra classe com a
mesma interface, e só após conferir que a licença do modelo permite uso comercial.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from verion.errors import DepthMissing

__all__ = ["DEPTH_SUFFIX", "DepthEstimator", "DepthFromFile", "depth_path_for"]

DEPTH_SUFFIX = ".depth.png"


def depth_path_for(photo: Path) -> Path:
    """Caminho do mapa ao lado da foto: ``sala.jpg`` → ``sala.jpg.depth.png`` (nome inteiro + sufixo)."""
    return photo.with_name(photo.name + DEPTH_SUFFIX)


@runtime_checkable
class DepthEstimator(Protocol):
    """Devolve o caminho do mapa de profundidade (PNG 16 bits cinza) de uma foto."""

    def depth(self, photo: Path) -> Path:
        """Mapa de ``photo``: mesma resolução da foto orientada, branco = perto. ``DepthMissing`` se não existir."""
        ...


class DepthFromFile:
    """Lê ``<foto>.depth.png`` ao lado da foto. Não gera nada: só localiza o arquivo."""

    def depth(self, photo: Path) -> Path:
        path = depth_path_for(Path(photo))
        if not path.is_file():
            raise DepthMissing(
                f"mapa de profundidade não encontrado: {path} "
                "(esperado PNG 16 bits cinza, mesma resolução da foto, branco = perto)"
            )
        return path
