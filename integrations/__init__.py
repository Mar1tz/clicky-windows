"""Connectors to the person's own accounts (the CI workstream of the Massive
Direction Plan): which services HeyTora knows (providers), how a connection
lives and dies (lifecycle), where its secrets are kept (vault), how it is
granted (oauth), and the first connector, Gmail send (gmail).

A connector is reached only through these: a token never leaves the vault
except to go in one request's Authorization header, and nothing here logs it.
"""
