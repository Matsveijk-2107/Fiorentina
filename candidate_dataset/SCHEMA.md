# Schema dei dati (FINTI, generati per il test)
Ogni file `data/<raw|raw_update>/<Campionato>/<match_id>.json` = UNA partita.
- match_id (int), competition (str), season (str), match_date (YYYY-MM-DD)
- home_team / away_team: {team_id, name} ; score: {home, away}
- players: [{player_id, name, team_id, position, minutes}]
- events: lista ordinata per minuto. Ogni evento ha: event_id, minute, second, team_id, player_id, x (0-100), y (0-100), type, e:
  - type="pass"   -> outcome in {complete, incomplete}, recipient_id (int|null)
  - type="shot"   -> outcome in {goal, saved, off_target, blocked}, xg (0-1)
  - type="tackle" -> outcome in {won, lost}
  - type="dribble"-> outcome in {complete, incomplete}
  - type="foul"   -> outcome in {committed, won}

NOTE
- `data/raw/` = lotto iniziale (24 partite, 2 campionati).
- `data/raw_update/` = lotto successivo: 4 partite NUOVE + 1 partita CORRETTA (match_id 1003, ri-esportata con un gol in piu e "corrected": true). Serve a testare l'incrementale/idempotenza.
- Dati non reali.