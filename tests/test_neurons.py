import json

from stationhouse_cli import cli

N = "/api/v1/projects/marin-neuron-atlas/neurons"
CLAIM = {"neuronId": "L40-N25546", "state": "claimed", "holder": {"github": "ada", "githubId": "1"},
         "until": "2026-11-02T12:00:00.000Z", "note": "testing reversal", "challenge": False}


def run(capsys, *argv):
    code = cli.main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_claim_sends_the_note_and_says_when_it_lapses(signed_in, api, capsys):
    api.routes[("PUT", f"{N}/L40-N25546/claim")] = (201, {"claim": CLAIM})
    code, out, _ = run(capsys, "neuron", "claim", "L40-N25546", "testing", "reversal")
    assert code == 0
    assert api.calls[0]["body"] == {"note": "testing reversal"}
    assert "You hold L40-N25546 until Nov 2." in out
    assert "stop the clock" in out


def test_renew_sends_no_note_so_the_old_one_stays(signed_in, api, capsys):
    api.routes[("PUT", f"{N}/L40-N25546/claim")] = (200, {"claim": CLAIM, "renewed": True})
    _, out, _ = run(capsys, "neuron", "renew", "L40-N25546")
    assert api.calls[0]["body"] == {}
    assert out.startswith("Renewed: you hold L40-N25546")


def test_someone_elses_neuron_is_an_error_naming_them(signed_in, api, capsys):
    api.routes[("PUT", f"{N}/L40-N25546/claim")] = (409, {"error": "L40-N25546 is claimed by @bo until Nov 2."})
    code, _, err = run(capsys, "neuron", "claim", "L40-N25546")
    assert code == 1 and "claimed by @bo" in err


def test_mine_lists_held_neurons_and_the_cap(signed_in, api, capsys):
    review = {**CLAIM, "neuronId": "L7-N100", "state": "in-review", "until": None, "prNum": 18}
    api.routes[("GET", f"{N}/claims")] = (200, {"me": {"github": "ada", "githubId": "1", "live": 2, "cap": 5},
                                                "claims": [CLAIM, review]})
    _, out, _ = run(capsys, "neuron", "mine")
    assert api.calls[0]["path"] == f"{N}/claims?mine=1"
    assert "You hold 2 of 5:" in out and "L40-N25546  (until Nov 2)" in out and "L7-N100  (PR #18 open)" in out
    _, out, _ = run(capsys, "neuron", "mine", "--json")
    assert json.loads(out)["me"]["live"] == 2


def test_who_shows_holder_explained_or_free(signed_in, api, capsys):
    api.routes[("GET", f"{N}/L40-N25546/claim")] = (200, {"claim": CLAIM})
    _, out, _ = run(capsys, "neuron", "who", "L40-N25546")
    assert "@ada holds L40-N25546  (until Nov 2)" in out and '"testing reversal"' in out
    explained = {"neuronId": "L40-N25546", "state": "explained", "holder": None, "explainedBy": "bo", "explainedPrNum": 4}
    api.routes[("GET", f"{N}/L40-N25546/claim")] = (200, {"claim": explained})
    _, out, _ = run(capsys, "neuron", "who", "L40-N25546")
    assert "explained by @bo (PR #4)" in out and "challenge" in out
    api.routes[("GET", f"{N}/L40-N25546/claim")] = (200, {"claim": None})
    _, out, _ = run(capsys, "neuron", "who", "L40-N25546")
    assert out.startswith("Nobody holds L40-N25546")


def test_release(signed_in, api, capsys):
    api.routes[("DELETE", f"{N}/L40-N25546/claim")] = (200, {"claim": {**CLAIM, "state": "free", "holder": None}})
    _, out, _ = run(capsys, "neuron", "release", "L40-N25546")
    assert out.strip() == "Released L40-N25546."
