class PartyHistory:
    """
    Infers premade parties from recent match history.

    Riot's chat presences only expose party IDs for friends, so presence-based
    detection misses most strangers. Completed matches, however, record a
    partyId for every player. If two players in the current game shared a
    partyId in a recent match (and are on the same team now), they very likely
    queued together again.
    """

    def __init__(self, Requests, log, depth=2):
        self.Requests = Requests
        self.log = log
        self.depth = depth
        # match_id -> {puuid: partyId}; completed matches never change, keep for the session
        self.match_parties_cache = {}
        # puuid -> [match_id, ...]; cleared each new game
        self.history_cache = {}

    def clear_runtime_cache(self):
        self.history_cache.clear()

    def _get_recent_match_ids(self, puuid):
        if puuid in self.history_cache:
            return self.history_cache[puuid]
        match_ids = []
        try:
            response = self.Requests.fetch(
                "pd",
                f"/match-history/v1/history/{puuid}?startIndex=0&endIndex={self.depth}",
                "get",
            )
            if response.status_code != 404:
                match_ids = [
                    m["MatchID"] for m in response.json().get("History", []) if m.get("MatchID")
                ]
        except Exception as e:
            self.log(f"party history: failed to fetch match history for {puuid}: {e}")
        self.history_cache[puuid] = match_ids
        return match_ids

    def _get_match_parties(self, match_id):
        if match_id in self.match_parties_cache:
            return self.match_parties_cache[match_id]
        parties = {}
        try:
            response = self.Requests.fetch("pd", f"/match-details/v1/matches/{match_id}", "get")
            if response.status_code != 404:
                for p in response.json().get("players", []):
                    if p.get("subject") and p.get("partyId"):
                        parties[p["subject"]] = p["partyId"]
        except Exception as e:
            self.log(f"party history: failed to fetch match details {match_id}: {e}")
            return parties  # don't cache failures
        self.match_parties_cache[match_id] = parties
        return parties

    def get_party_json(self, Players, current_match_id=None, base_party_json=None):
        """
        Players: list of player dicts from pregame/coregame (need "Subject", optional "TeamID").
        base_party_json: presence-based {party_id: [puuids]} to merge with.
        Returns {party_key: [puuids]} containing only groups of 2+.
        """
        puuids = [p["Subject"] for p in Players]
        teams = {p["Subject"]: p.get("TeamID") for p in Players}

        parent = {pu: pu for pu in puuids}

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a, b):
            if teams.get(a) != teams.get(b):
                return  # premades are always on the same team
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[rb] = ra

        # Presence-based parties (friends / own party) are authoritative, keep them.
        for members in (base_party_json or {}).values():
            members = [m for m in members if m in parent]
            for m in members[1:]:
                union(members[0], m)

        # Collect which match ids each current player recently played.
        in_game = set(puuids)
        checked = set()
        for puuid in puuids:
            for match_id in self._get_recent_match_ids(puuid):
                if match_id == current_match_id or match_id in checked:
                    continue
                checked.add(match_id)
                match_parties = self._get_match_parties(match_id)
                by_party = {}
                for subject, party_id in match_parties.items():
                    if subject in in_game:
                        by_party.setdefault(party_id, []).append(subject)
                for members in by_party.values():
                    for m in members[1:]:
                        union(members[0], m)

        groups = {}
        for pu in puuids:
            groups.setdefault(find(pu), []).append(pu)
        party_json = {root: members for root, members in groups.items() if len(members) > 1}
        self.log(f"party history: merged party json: {party_json}")
        return party_json
