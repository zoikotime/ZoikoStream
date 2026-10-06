"""Custom-domain DNS evidence (services/domain_dns.py), against a scripted resolver.

No network and no database: `q` is the one function that would ask a real resolver.

    correct TXT + CNAME            -> verified
    TXT with another token         -> txt_mismatch          (another claim's proof never counts)
    no TXT                         -> txt_missing
    CNAME elsewhere / chain        -> cname_mismatch / ok through a chain
    no CNAME                       -> cname_missing
    hostname does not exist        -> nxdomain
    resolver timeout / SERVFAIL    -> temporary, never a definitive failure
    resolvers disagree             -> not verified (propagation), never a pass on one answer
"""
from app.services import domain_dns as d

HOST = "events.example.com"
TARGET = "cname.zoikostream.com"
TOKEN = "a" * 64
GOOD_TXT = f"zoikostream-domain-verification={TOKEN}"


def scripted(records: dict, per_ns: dict | None = None):
    """records: {(name, rdtype): (outcome, values)}; per_ns overrides for one nameserver."""
    def q(ns, name, rdtype):
        table = (per_ns or {}).get(ns, records)
        return table.get((name, rdtype), (d.NXDOMAIN, []))
    return q


GOOD = {
    (HOST, "CNAME"): (d.OK, [TARGET]),
    (f"_zoikostream.{HOST}", "TXT"): (d.OK, [GOOD_TXT]),
}


def run(records, **kw):
    return d.check(HOST, TOKEN, TARGET, nameservers=["ns1"], q=scripted(records), **kw)


def test_correct_txt_and_cname_verify():
    r = run(GOOD)
    assert r.ok and r.cname_ok and r.txt_ok and r.error is None and not r.temporary
    assert r.as_json() == {"cname_ok": True, "cname_found": TARGET, "txt_ok": True, "txt_present": True}


def test_trailing_dots_and_case_are_normalized():
    r = d.check("Events.Example.com.", TOKEN, "CNAME.zoikostream.com.", nameservers=["ns1"],
                q=scripted({(HOST, "CNAME"): (d.OK, ["Cname.ZoikoStream.com."]),
                            (f"_zoikostream.{HOST}", "TXT"): (d.OK, [GOOD_TXT])}))
    assert r.ok


def test_a_txt_value_pasted_with_quotes_still_matches():
    r = run({**GOOD, (f"_zoikostream.{HOST}", "TXT"): (d.OK, [f'"{GOOD_TXT}"'])})
    assert r.txt_ok


def test_another_token_does_not_verify():
    other = f"zoikostream-domain-verification={'b' * 64}"
    r = run({**GOOD, (f"_zoikostream.{HOST}", "TXT"): (d.OK, ["v=spf1 -all", other])})
    assert not r.ok and not r.txt_ok and r.txt_present
    assert r.error == d.TXT_MISMATCH


def test_missing_txt():
    r = run({(HOST, "CNAME"): (d.OK, [TARGET])})
    assert r.cname_ok and not r.txt_ok and not r.txt_present
    assert r.error == d.TXT_MISSING and not r.temporary


def test_txt_alone_does_not_verify_without_the_cname():
    r = run({(HOST, "CNAME"): (d.NOANSWER, []), (f"_zoikostream.{HOST}", "TXT"): (d.OK, [GOOD_TXT])})
    assert r.txt_ok and not r.cname_ok and not r.ok
    assert r.error == d.CNAME_MISSING


def test_cname_to_the_wrong_target():
    r = run({**GOOD, (HOST, "CNAME"): (d.OK, ["something.vercel-dns.com"]),
             ("something.vercel-dns.com", "CNAME"): (d.NOANSWER, [])})
    assert not r.cname_ok and r.cname_found == "something.vercel-dns.com"
    assert r.error == d.CNAME_MISMATCH


def test_cname_chain_reaching_the_target_passes():
    r = run({**GOOD, (HOST, "CNAME"): (d.OK, ["alias.example.net"]),
             ("alias.example.net", "CNAME"): (d.OK, [TARGET])})
    assert r.cname_ok and r.ok


def test_cname_loop_is_bounded():
    r = run({**GOOD, (HOST, "CNAME"): (d.OK, ["a.example.net"]),
             ("a.example.net", "CNAME"): (d.OK, [HOST])})
    assert not r.cname_ok and r.error == d.CNAME_MISMATCH


def test_nxdomain_is_reported_first():
    r = run({})
    assert not r.ok and r.error == d.NXDOMAIN_CODE and not r.temporary


def test_timeout_is_temporary_not_a_failure():
    r = run({(HOST, "CNAME"): (d.TIMEOUT, []), (f"_zoikostream.{HOST}", "TXT"): (d.TIMEOUT, [])})
    assert not r.ok and r.temporary and r.error == d.DNS_TIMEOUT


def test_servfail_is_temporary():
    r = run({(HOST, "CNAME"): (d.ERROR, []), (f"_zoikostream.{HOST}", "TXT"): (d.OK, [GOOD_TXT])})
    assert r.temporary and r.error == d.DNS_ERROR


def test_one_resolver_timing_out_does_not_block_the_one_that_answered():
    q = scripted(GOOD, per_ns={"slow": {(HOST, "CNAME"): (d.TIMEOUT, []),
                                        (f"_zoikostream.{HOST}", "TXT"): (d.TIMEOUT, [])}})
    r = d.check(HOST, TOKEN, TARGET, nameservers=["slow", "ns1"], q=q)
    assert r.ok


def test_resolvers_that_disagree_do_not_verify():
    """Propagation in progress: one resolver sees the record, one does not yet."""
    q = scripted(GOOD, per_ns={"stale": {(HOST, "CNAME"): (d.OK, [TARGET])}})
    r = d.check(HOST, TOKEN, TARGET, nameservers=["ns1", "stale"], q=q)
    assert not r.ok and r.error == d.TXT_MISSING and not r.temporary


def test_resolves_checks_a_and_aaaa():
    q = scripted({(TARGET, "AAAA"): (d.OK, ["2606:4700::1"])})
    assert d.resolves(TARGET, nameservers=["ns1"], q=q)
    assert not d.resolves("missing.zoikostream.com", nameservers=["ns1"], q=q)


def test_record_names_and_values():
    assert d.txt_name(HOST) == "_zoikostream.events.example.com"
    assert d.txt_value(TOKEN) == GOOD_TXT
