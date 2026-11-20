"""Les tuiles contiennent exactement les habitants de leur emprise, lisibles par un décodeur standard."""

import gzip

import numpy as np
import pytest

from projet import donnees, tuiles

pytestmark = pytest.mark.skipif(not donnees.CARREAUX.is_file(), reason="carreaux non préparés")

# Tuile de zoom 10 qui contient Orléans (longitude 1,909 ; latitude 47,902).
Z, X, Y = 10, 517, 357


def _varint(octets, i):
    valeur, decalage = 0, 0
    while True:
        b = octets[i]
        i += 1
        valeur |= (b & 0x7F) << decalage
        decalage += 7
        if b < 0x80:
            return valeur, i


def _champs(octets):
    """Décodeur protobuf minimal : (numéro, type, valeur) pour chaque champ."""
    i = 0
    while i < len(octets):
        entete, i = _varint(octets, i)
        if entete & 7 == 2:
            longueur, i = _varint(octets, i)
            yield entete >> 3, 2, octets[i : i + longueur]
            i += longueur
        else:
            valeur, i = _varint(octets, i)
            yield entete >> 3, 0, valeur


def lire(corps):
    """Points d'une tuile : coordonnées dans la tuile et attributs."""
    (couche,) = [v for n, _, v in _champs(gzip.decompress(corps)) if n == 3]
    cles, valeurs, points = [], [], []
    for numero, _, v in _champs(couche):
        if numero == 3:
            cles.append(v.decode())
        elif numero == 4:
            ((genre, _, contenu),) = list(_champs(v))
            valeurs.append(contenu.decode() if genre == 1 else contenu)
    for numero, _, v in _champs(couche):
        if numero != 2:
            continue
        entite = {n: c for n, _, c in _champs(v)}
        etiquettes = [x for x, _ in _iter_varints(entite[2])]
        commande, zx, zy = [x for x, _ in _iter_varints(entite[4])]
        assert commande == 9
        point = {
            cles[k]: valeurs[i] for k, i in zip(etiquettes[::2], etiquettes[1::2], strict=True)
        }
        point["x"], point["y"] = (zx >> 1) ^ -(zx & 1), (zy >> 1) ^ -(zy & 1)
        points.append(point)
    return points


def _iter_varints(octets):
    i = 0
    while i < len(octets):
        valeur, i = _varint(octets, i)
        yield valeur, i


def test_une_tuile_contient_les_habitants_de_son_emprise():
    points = lire(tuiles.tuile("fins", Z, X, Y))
    dans = [p for p in points if 0 <= p["x"] < tuiles.ETENDUE and 0 <= p["y"] < tuiles.ETENDUE]
    c = donnees.carreaux()
    mx, my = tuiles._mercator(c.lon, c.lat)
    attendus = (np.floor(mx * 2**Z) == X) & (np.floor(my * 2**Z) == Y)
    assert len(dans) == attendus.sum() > 1000
    assert sum(p["p"] for p in dans) == pytest.approx(c.pop[attendus].sum(), rel=0.02)
    assert "45" in {p["dep"] for p in dans}  # Orléans ; la tuile déborde un peu sur le Loir-et-Cher
    assert len(points) > len(dans)  # le débord apporte les voisins des bords
    assert all(-tuiles.TAMPON <= p["x"] < tuiles.ETENDUE + tuiles.TAMPON for p in points)


def test_la_distance_entiere_classe_loin_et_couvert():
    points = lire(tuiles.tuile("fins", Z, X, Y))
    c = donnees.carreaux()
    mx, my = tuiles._mercator(c.lon, c.lat)
    attendus = (np.floor(mx * 2**Z) == X) & (np.floor(my * 2**Z) == Y)
    dans = [p for p in points if 0 <= p["x"] < tuiles.ETENDUE and 0 <= p["y"] < tuiles.ETENDUE]
    for seuil in (5, 10):
        assert sum(p["q"] >= seuil for p in dans) == (np.floor(c.distance[attendus]) >= seuil).sum()


def test_les_deux_niveaux_et_les_tuiles_vides():
    moyens = lire(tuiles.tuile("moyens", 8, 129, 89))
    assert len(moyens) > 500
    assert tuiles.tuile("fins", 10, 0, 0) is None  # personne au large
    with pytest.raises(KeyError):
        tuiles.tuile("fins", 12, 0, 0)  # zoom non servi : la carte agrandit le zoom 10
    with pytest.raises(KeyError):
        tuiles.tuile("autres", 10, 517, 357)
