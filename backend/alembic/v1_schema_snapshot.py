"""Frozen SQLite DDL snapshot used exclusively by revision 0001_initial."""

from __future__ import annotations

import base64
import hashlib
import zlib
from typing import Any

SCHEMA_SHA256 = "9fac4f66fe675c9c1457942f14fc8ccbe56209ae7a1c9f9df24dbc149b4b195d"

# zlib-compressed, base85-encoded sqlite_master DDL from the accepted V1 baseline. Keeping the
# immutable payload here makes accidental coupling to live ORM metadata impossible.
_DDL = b"""c-qw)+j84B_PO~APG4fp_#w`&)1B$KGm65tp1N`?E4%Fz2PTgs)Fi<Ipl$W*_W-;BBzRGzIPIH=%fY$d04-MjeC>Pd`THB+BUu*HkVwkXK!B7q-3ssQ)mL9X!2WCRzw_1N$NB2);_ZKC-tu<sEkEAeoO`dndL(fU!5@O9-rE0dZO!FbL?lE3k>2%k?f>Aftm$7?*FVo!zk5IVzk6r;nAxk@x33l!R=M0EIzk{?8XX$dxcuv#jY=+n2LtnxJwO`7j6LL8up?quuYC7!du1s222Tw+$dQE{hA51QkO&7)?O8zX1&ebD0X%1vgXmkw;SrHH&*?CbMl&_`{q4%X{$Z)c&Ps;Nyp{jGzw(z0|ISlmLa+92m)-~e#z$T+=68$v2Vd>~xV--Lqwk${nAMd{$dU{sq~Sgw_cW$*A5aO&n3#-h5o~BmwaN|24W4CJxfqWWb!i08<Jwg+XK}l{Td(H0+{^#0k=BsBits-4&OPJA4y*fiS-f{H>J!EgyRF?*nN1@|r4~~Ea?VJUkSusaB^i-QhDrg0V&f!y7=%DLV$`n$8VHp!)Prv?FKrMJh>+7vZSHEwmXWwSnsLfFvY|QYtqt=D=yvBe!!eyBJlxSoh`jf=w>SQLX&=l)LFKb)!}aLi93Am1hd5TJ>bumUlzD9kU0pc$-0IXm0L5-BEcv<k(O>-Jot0nj{q_HRf9YL#^W_Kcau`A_vJyu9eZBO~UWbgs>+{z%$rzU;mH1oanfkyGh969;2Z>+LhOE{kr^jZ?B@{X)yYdn7>&3bazso`kc58Yv*vj-QV0v&eh{`xE5PEPr77p{B>h6Y|o5|)u-zFrvk0HRsq2EEcOrcWnX8#~Q83zu3<`nG|P9ED#;K6~;`T-qLuNOPW2bfq)2!s=mb{Mp^OXFUev;M73S<iEb&}um#-~`asM#F}CqHx>M?g=!0vY!}IH`K~f&kj_FFz4DJ6@XmyD#jtP<5S#yd3-6`7E)TuDbwFvYC*?9cv^Vs4E>L!Vex?xFxm$j#+7@do3b>)iQ-f5O#vbiU~)|1=>^>TnAma=z#{@VaZ<M7EOAJ+C~3yTjcjz*W`gBBgx+l?&jV$PJaS}bBWcQ5ee$`(ubT)9zGKOFgv!w?apZd%p{o9~Y91T)e@NvM5gPANI-3~6DMe4rLJ|kc%{6_CH&gg~`n0>^ZG%!*vmz=&n#D8)jFD_Z<2uc7o$>_mfZRa;xdj<0hxrHycrTo*%Vkl{3l&d9kfA7>ZD!RWX##(-6#Bb8BqEtCAQeey$kGkno}mBZ3@ZYa6f?9ggR1a?Ji78O96N0;Y4+3wNFV>~n&cY?)D|}@kQ11Er>XXCSKj#I@4Po%C4pxu1FUr=IxO5Zy#vtNI4P4jjkl_@IcGI&SJ^L7^J+#k^h|Jj{jBSF!GWcwA9OD}Af%np+xBKP14y1St(j83ndxJHvhpID(=fz9+@V7T>F1tNVs^cW23t_($d;i=mJ?7-4Xn2%B{BX|-(Mixg=f`NX|kjdx1$%!eFnYVb4>0N6iOX!+6oe~3<8lS3E}&m5OZJ#+M#I+DR2#egF<+WtYVc-RvYh3sYz6nIK2r-xd@CBzP+Tut?%|ACGj2$CQ<k9T&slSc_?#q#6d>(F(XkAFS)9=)iUJwL&TnnCDTl#&BvyT*f8weGdWX#;i>0dhZKjsn7l6y#vIb-qEkgQks`_~TVbi(;jdX&_C?2VqCRINd>~t^;ob38CBQ+Q7vT;PGAuA%nguG)NlDUxy1Z!ly$H>M=jn)v=!?CcC|u69?v+N2D&xq8g4BEDN}j3I92F<UdU8l(DwSwh*_BDv4VZf8Hy(5Pt|Bz2i$j8hMg8Wivf`)}fi8;uI%!i!S|V?UCxVBlk_%;O9n@Y|_C)&Fu6toENs%zjlRVbaGBQfHw<t19%&9{#VL0~bVY<P>IFF@OBCuQ{%c0$ez$k@*q!RvTjmN4iFvV7S0&7gWf`p{G4wqVhWx#CZ^M+`vT;3QmvvzY>w<`0r>RORp=x&tK#TbgNR;a`s$35rTdQ+Zk59&jV-k55KSzR*FCzfLo+~?bEHP8OQI$7`0jj^g_R(H@i2vAp5ZrobhT4C23E|Zf~p%F3k+bq@ome62F^(74cDmH0`puuBe)5nlXdX;*WhgP44AnKvw>Zjp&lOEk;vHdVf3SHxAgyA-<@2;>o>&L$h#<H!3h(jPYW>~>A4ATJFjE>)}RlsAqfp8x-^|Wn9S6*dA8j{O2)VS?fjETl$-PIPZCoKBCHzgD|$FHo_YUKecFq<jfbf_vkf|Do{%`?jz;J(V}-DI0vEz(_No8v7tFK})hU3!@V7=DUV8}6P$(pN7a>NWwj2l%oCSg6cqLq3qE%FoKOIa?ZLN3uw3J%)HSxx;De!b9(kI|&_@a0N$5TxN@-nRd=ICi*kzjL4m%5ZnRckSBrIk++xc`txu47X<fvm9Dswk7Mh}>K1;uh@Ae)W=fOfm^U&<y=WOP3yUUkL&T&<CjCv58e)O=6ktPF7bkk2(NTi>?z((UnUYNG*l2HIwBDa^uL0>+waJId%ZyW9Elw+)s@1)vPEkjN45^(AbaG1q9W7*vBPAM@8Wt9hULu`%;@DN>FjGF!S&p&g6TuD=J`rYDXFJM1$%PFpsdVs&PDr>T=@w$P9lgIk@L}$_=W6kz<71R>F<VtGzvA{M2l$bC!_n`cj)@Ue1Z88|-9svJDt9n5oA|C5qo4QIwG~NB8FjE8cl{kZY|+*C9IqqSDqlKPk}&!d_e*CH<K|Z6mB%Ftf6tef9qE@Z@P|!WUwlhMT*=I76$r)Z4tMVAGa|&3dh(+G!SDia9h`Uf3eUPnXHivM+}Flx&RR1StzAp9p6|e;3UU|}hIu>ww5k7<WjoB+jqm9ObL7LRV;(k5cyHt0d&;|C>x7Y~XbH$28@P4hAo5uDUk<AmDurGcee9w&icTvg_YhmabsAl&QgyXbx3&*zbdMW}iw~u-xTbU)L_oYz4b@z-gcRKnc$UgSVrQGPB~8t&`ua?NX|T#&XkZ&cW2?vVGFQ$22T0B7QU"""  # noqa: E501

_INDEX_DDL = (
    "CREATE UNIQUE INDEX uq_one_active_timed_session ON learning_sessions (session_mode) "
    "WHERE session_mode = 'timed' AND timed_state IN ('running','paused')",
    "CREATE UNIQUE INDEX uq_one_current_roadmap ON roadmaps (is_current) WHERE is_current = 1",
)

_TABLES_IN_DROP_ORDER = (
    "verification_evidence",
    "verification_records",
    "recommendation_snapshots",
    "generated_reports",
    "daily_reflections",
    "learning_sessions",
    "competency_status_events",
    "competency_states",
    "exit_criterion_definitions",
    "exit_criterion_identities",
    "competency_ability_items",
    "competency_understanding_items",
    "competency_prerequisites",
    "competency_definitions",
    "competency_identities",
    "tracks",
    "phases",
    "roadmap_versions",
    "roadmaps",
    "import_records",
    "export_records",
    "operational_backups",
    "discipline_profiles",
    "application_settings",
    "auth_sessions",
    "users",
)


def _ddl() -> str:
    value = zlib.decompress(base64.b85decode(_DDL)).decode("utf-8")
    if hashlib.sha256(value.encode("utf-8")).hexdigest() != SCHEMA_SHA256:
        raise RuntimeError("The frozen V1 schema snapshot failed its integrity check.")
    return value


def create_v1_schema(connection: Any) -> None:
    for statement in _ddl().split(";\n"):
        if statement.strip():
            connection.exec_driver_sql(statement)
    for statement in _INDEX_DDL:
        connection.exec_driver_sql(statement)


def drop_v1_schema(connection: Any) -> None:
    connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
    try:
        for name in _TABLES_IN_DROP_ORDER:
            connection.exec_driver_sql(f'DROP TABLE IF EXISTS "{name}"')
    finally:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
