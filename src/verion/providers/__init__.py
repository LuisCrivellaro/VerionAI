"""Adaptadores de geração de cena (spec 3.4).

Este pacote não importa nenhum adaptador: o ``registry`` carrega cada um sob demanda, para que um
extra ausente (ex.: ``google-genai``) afete só o método que o usa.
"""
