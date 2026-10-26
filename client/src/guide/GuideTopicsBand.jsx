// Homepage band for ZoikoStream Guide: the six help topics from the Guide page design, each a
// link into /guide that starts that topic (/guide?topic=<id>). Static — no Guide API call is
// made here; the Guide page opens the session only when the visitor arrives with a topic.
import { Link } from "react-router-dom";
import { ArrowRight, ChevronRight, ShieldCheck } from "lucide-react";
import "./guideTopics.css";
import "./guideBand.css";
import { GUIDE_TOPICS } from "./guideTopics";

export default function GuideTopicsBand() {
  return (
    <section className="zgb" aria-labelledby="zgb-title">
      <div className="zgb-head">
        <div className="zgb-ident">
          <span className="zgb-avatar" aria-hidden="true">
            <img src="/zoiko-mark.png" alt="" />
          </span>
          <div>
            <p className="zgb-eyebrow">Zoiko Stream Guide</p>
            <h2 id="zgb-title">
              Your Guide to <span className="zgb-grad">Live Events,</span> Anytime.
            </h2>
          </div>
        </div>
        <Link to="/guide" className="zgb-open">
          Open Guide
          <ArrowRight aria-hidden="true" />
        </Link>
      </div>
      <p className="zgb-lede">
        Get instant help with joining events, fixing playback issues, captions and accessibility, replays, and more —
        all in one place.
      </p>
      <ul className="zgp-topics">
        {GUIDE_TOPICS.map((t) => (
          <li key={t.id}>
            <Link to={`/guide?topic=${t.id}`} className={`zgp-topic zgp-tone--${t.tone}`}>
              <span className="zgp-topic-icon">
                <t.Icon aria-hidden="true" />
              </span>
              <span className="zgp-topic-text">
                <span className="zgp-topic-title">{t.title}</span>
                <span className="zgp-topic-desc">{t.text}</span>
              </span>
              <span className="zgp-topic-go" aria-hidden="true">
                <ChevronRight />
              </span>
            </Link>
          </li>
        ))}
      </ul>
      <p className="zgb-security">
        <ShieldCheck aria-hidden="true" />
        <strong>Your security matters.</strong>
        <span>Never share passwords, PINs, secret event links, or other private account information in chat.</span>
      </p>
    </section>
  );
}
