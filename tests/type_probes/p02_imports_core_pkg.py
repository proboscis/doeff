# README の Quick Start と同じ取り方: package doeff_core_effects の頭から effect と handler を取る(型の報告の抜け 1 — 直った)
from doeff_core_effects import Ask, Get, Put, Tell, reader, state, writer

_ = (Ask, Get, Put, Tell, reader, state, writer)
