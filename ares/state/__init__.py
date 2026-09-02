"""Accesso allo stato appreso, in sola lettura, e sue primitive.

``stores`` e' l'unico punto da cui si leggono entita', intuizioni e sessioni;
``lock`` espone il lock cooperativo condiviso/esclusivo dello stato;
``platform_files`` uniforma permessi e lock di file fra POSIX e Windows.
"""
