"""Independent collection, bounded subprocess shards and exact-coverage evidence.

This is a companion diagnostic runner, not a trading entrypoint or a replacement
for the existing CI. No retries, xfail additions, failure allowlists or synthetic
GitHub PR context. Ordinary pytest assertions and deadlines are untouched.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import signal
import sqlite3
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
import zlib
from collections import Counter
from pathlib import Path
from typing import Any

PROFILE = "Q7A_OFFLINE_DIAGNOSTICS_V1"
PLAN_SCHEMA = "Q7A_CI_PLAN_V2"
# Frozen run 37241958124 / attempt 1. Positive durations only: never outcomes.
# Compressed canonical JSON keeps the immutable planning input in the allowed tool path.
FROZEN_TIMING_SHA256 = "3526d43140613fb18fc1814c5108e55c794330f1f501b9e29743e1ddb6103bd9"
FROZEN_TIMING_B85 = (
    "c-q{(eRJDLk~jEOI{eq&ZL1IOASUiE!t2)T_{L-RW4mW}R*!=LlAwe!2(a-YS#w|hDl@AJRUj=Bs1l%fdUm^QkwRt_Du0#l`TxBt"
    "tNM@@`M=UOugX_{dbQ2+;(v3034i)OeyTQ2R^Mm4^`ZISfBLtdJjZc<Iy65Wv-+ntYucZhHm%$4_*0$X_g?+{s_NQpb;z1^*JQg_"
    "f7+)-ll}av&bC#(YhL~7=H?arV)K8(zt?GTyi47+yWVGM+p&lIa(w*LpXj6Q*LBk!kL-sHV{6SwHaYvnb++GU+cv+?*2g+Or1j%^"
    "mu++S&a0o@pPipyZPIpocf~k$UVZubQgYj>DDWZcCf&0~r|sQ3-R+un*&VVv-!fkI`~2IhpM6He9tl`sRkKxfWxlO;*?O0=t!k?}"
    "&l;fRO{BHU)7!FYTE;kg)@@#{^Jaa|sBemFy{~FUFKyd8-*hc|xyP!>TfTw*M2gurSjc*x7g?DevUQyus(W?}9`bTmJv8gQyk}=@"
    "x8B~Rb-HcY+XQNXC2H)jvGu_bVcVOQyLFqhGuWnwBTxw^Q(=6TX|diGRg=MyEUSki+ude|tYj~=$kMu5Gcs&bnIPO|>}lODXIuK}"
    "=am!w96b-+I{PO(BkX8a<-SPU^`@@AX7#I|llbTOc_|*Va+jC4>`d)|pzB7H1@1zM4|T=p9q;P2$$(%uS^RFheBD%Cz0H^;mTj7s"
    "P5<?Z!Ar`-PX692c4(#wpAaV@dUe&oiLCP@9?cC~>qA=a7;f#;u4ql9uFjjU>x>;a#`>nHwqF5B5MF3Sj0@OewqCa1H`%vr+aYHu"
    "BIp+r5gbSQ!1lXkvRZC4m3TM{arrZqbTkWTO=6I4JPTd8aLa98<g{-51;)o0lRI)82OWFWHHewAcGm)BcH7q(Lxc4uE2@X}wySFd"
    "V<uz(8+4uCr3IpJoJ^Ktm1e%n4#%nmI_dFPFqNaq0Dsv<+Z~I1o3`0HyJsIW5O%Bxv%o)bZ5H_<Z=0Jd*P6_Jhk|2GF26&-bzE!m"
    "_Z<?!&!II@`yPp3LTj@09TK1GF0&>z-=Tre_nz<hfCOJ`U2xrT#{`XOA$0|cA6}+MZh15W0HjvBjui>Pl_152k=j(>)^%FmW;ZUE"
    "!tgn1IL;OOe`}KRJvzR3<$KnI<$F|utH4ZJ*sEm<J+sKtBYVfvFl2-j#c{_~@!lTvCd*FKvI{M{eUzlz^eY~{Y@6*e&|;W%Qwt66"
    "E-%|lCCPu<Tx+tTVBg4gpeBOV#!MY{!{jX<_omx!*}C^#F%&A-205mdf>yj?M!Ya+4ta9`8pCU6485#u*bju!CQRJNHC*IySi~JJ"
    "_L|?~uNc8_B1zEp(I}M9=9&Scjyoy0RoM)&4{WWGCUs*d=jFCK9E+^Y)JL<gFjXJkCuj}C3_GxCBVOrH1W!l;WAF#fgsRhZuhj3d"
    "B7Izw+06j5SXX7n{>pZKlQL_~3pDw=fItDPg7E?xENt2~ZSFw91L?>?8LHuBHAnV7LECZ@P2Mh`aLAZhw1sP9TXegOzy$60t}ao+"
    "_IXz9V0Uz9$OX?XD`s=0J6Jt?V0vHWyJp?y<)b*-4_SVD2QOoj7GRh#DxRkamG@&RO@0enFA6HW2zaTCHhVXq6_@LWyuAZ)+SggO"
    "zD<vGdoi@|VojbdA;r)qWf)N8$76a+_a8fJOhVD^QD?i3;ZpWTm$#4X`JpB}r_ZmHN2dim`Vp9e!^jq~ecUoAWqSR-st;iK*Lk&L"
    "2N@3TzAEx+t(zE(psQb1KcINb*@3-<7sQs79n)Ha0$5v-vhM-9mlD$1D~bY5PM)T+&Fd}O4mb!UH_A~@?@t`F6!!F{a67>nL3V?4"
    "F_B%GGm`-9+nQZ~fGq4$4S?yPBa@#WK2(YnC&Zp15IYIJ?$r1Ju^R6rw)-F=jdX%Gcj*qyA%;CElcK6Wd1!utH__81=rS^hp%N<}"
    "s;=0rH{Bz9H_ZHc^>e6OJN}c;lmdbrt-og3vFRU%8^xBwtV;U4L3_$wMVg#EO#=O4(*MF8bVYWXZrKiP*=uI^*)8`4C@;X#W#wra"
    "VEK}+!5|(LQEm)3AP_r-nC$C9lGp+qKNukw>;SSO(rtRFZjS8SXDDvq;aqRnQo!d)yOaa&vg3NWOq5`t*M}Tni$9~0!ps&X&P{LH"
    "B0KOd0)K>9038%NkY{a0U@E|$<ZZ|<0rZ(hBLqP#AD*(yXhk{RwQ!K?3_}y>YKM2Q8gcfS0JS;W9K1tuZ~EK1ziq*bj^6-sU)dkv"
    "td0=}%{3&>P}SbCzkn9Bui+)<CB5t%?%eLcMZzB;5(;v2u@EX9_~_9My5UEfea7U6(Z6GI({7k*12>{SLN+9^f&U3!5SWM3>noYr"
    ")%hOQlq4gBME*7*>h=z-40u&yB@cHXm-1`u6M%SFO*BGM1h%pphzZ_ZeuM`xpsnF7Z0o9N`Zop2gFoihkiac0jfM#xdnp&J!VQBF"
    "f--m&^6eA;ic@$Z_3RRKMhFd_Qla^RrdaNZOh|aIR<sZKHXC_w@T-k)R1QWT(>wpku2FW90I@b*zny6TS}(t90Dco^gs>2(gP{WV"
    "UXx{|ya`F$VwTK7E{_=~?g-)FHwM!dz#7_Qdr-~U*4*a+09&>ei2?)(Y*}<6riV>_OKRO}jGPd~CcW>;rh77=6*%Vi%9#`3#{LL#"
    "IYUioDd;8UHV|y2^q-2WWfhJP9fc6w9giIGaUG9?!-0B_JL1t?8imFQN4UsK5XZJ!XDJh$GGm94!qV*N@dzPl!Hrxq{fz00LdX^S"
    "O>7E3X7ZG|41+Hhmo@_-FWK?$b<%B-9+<EfRrM7OeYO;QC7^{jOkHIsd5oC!_j*>>RZX}gLiFg3=X&>snH3nDWZQJPplHEqdbGem"
    "E+BH#P*uNNAi5IloD`zqivHHL726!r+hALYN6>Xfocwz~bk~$Ck!~2$wvUQQBTv%4t_~t}8jLyb_x^eX?~)lyX>Vv?iUqyK_71Ge"
    "hb;RlU-8YG*YAIQcfEf5{`2+6zrTLBcEk1G-oF2WhM_wzY2Oi~e`JDmc>$7KK!B%bLi~7-)(BaI4TOPO5D|v@=pIldB$)JMZYc0?"
    ";n`p}$VwB8aZmmi(;2dFXlXXR7ZW`x+dE0Y)BRlINsy9SSG#Tt86NnKqbJ4fM^BWADRot~jLBwwSGD{68$;LaT@IN(uC7U)kl1E$"
    "YJ`cgt=NB=$$?&CcBT{)J!OUhyKf4i#?cLho*>2^IJ5rfvMyt?+g2UhwH*qlV}o|~4J-zx{a|pqrF~30b`RG1zd*@DR#cbAJ&7{0"
    "dLm1c87V8pYL(h7AjeL!L5_ANeI0kWxVpo6GVvoj{{_0j5M7mf2viaz%YKKPwS1>Q2h{{FoJyQMwdkV-Kud~=cn^*C>w<wm_eW1>"
    "09}r|e|8PrHrzGHHlG<{%pfYHw-D5g9oS=VD`S)ceR!b_9@>XWvG7R0#X}FSG4_WN>`}&wP|+ykNA3_m55Dey3!diYz>xDc8*-vO"
    "lBsdzsIS4P$kaFVDEG$&x2`GEW5d7!qAjs0dAYZA{OcU{9Pdr06ZCQ^wKRr#Om5O*7+ji0yejv34eGtH{Dnq??;TGnExDz)ue-Y^"
    "(NyvWN`g5?R!uh$UU!5ho{Y)NIYwSjZJ0!z89L~?=LmZT?q&9R$wNv8ZhtH>_;r^tfT+_gT>vTqL{k_zFoPh+ID-f+z^WBRkNecX"
    "ODGOM@p|Z5;dk~lAeh95=JFA~BQlVsuPohSh*>q3!J;AiCGa#Pn8t)WZMqb0s)}8N<+^=5;_(3EN_whY{iYAi2(y#NR@egQ9l)0Z"
    "S7XCHc3`XA^N0r8KzJCKp>m=u2Zl@(pokT!)Ben=jm7=D*Sa`+yPTOu82a=ab4h2?a|B$+&=B~MB(S59m)X67=$U5XxE4g6q^8f7"
    "0C7u)BHj#gDny>=%3RWgP%&|43&jA;nqMQ#810kAPDLxUAm2i#;0X#SRm@W9j>s4IVPN6hYp_N~f_yIS9E01HxrB2xqbx+Bbb=#!"
    ";N#`7iry6nJ^*t$$6!CFDwRC`vhhT~rD80|w4WfZ4VTw7iWY%|no!`F3o#v%KB%O*v;n^0#TQC=1C_R?g&EX<+uob8%~TbkuDfF^"
    "Q;?XxLQ3KW12m{2_qhe&M+h|04NZvQzlKPt+r9Ca=c}I0-V4R4krJAhy9_+Ul-j7t;V4ovEi9#DNY}{$DjtRl(rw{URODM3o;CFn"
    "gUs&eNkP7VLQ$o=J`X>`Ym0j%`a4n-JcwH|)MP81LRXOFv_x`*9+sQ-<UUDV9OMRELHw72d)JhPz;^k5wAbQ4Eh#&OPn!zz=C?&!"
    "GaW^7TM(2p(9ZXuOv5pT1R%I&8KQ0>LuW@|mp-ij*`Tqc$<1m6wS8AeWst`o`Q^Q-b4WuQrb>-@__-<t8GeT-qX-jWS^;N*T^5h{"
    "^|~tXG=vkWs{}S_SyF6R(ubQ0TmoqSG{e-y`1^$J^Xx&vHD+ovi;n3(DY{A-h{C(cLE~ia28<!GGQ0<esZr6Tgrw({9Y7It2LEqE"
    "X)}0jh&!Lxtw9t|6Q+xaNnet9;^wjCD0-BZ8{J0C%ESy9KvvXc-6;g0IENe1Xj96`(xmF*hr>CnA?POE*kW0_JG(2%9ov_X1z{3v"
    "vXxv)Q<jMF93UQJQ7Ec91=AHlK`+wBp}bv8@XT=+>#6p86fr7_3#U9CLh0%ulp5ED-Ro>?Dbz0(DPAOcxcXSXuJx^^(r)gc29N2T"
    "U0TS<6ByNZRk5SY(hN^iOG#D}1rDr25r`G884&~KFN@MBDxOAE)k6(#=*hX5HdTsA6(0OUm}7bhF47N&HCjUM-5*Gy%;i)|I<a=`"
    "Y%S%QJ4?WOouW+*kp%Bayc|mc-Wk9%du<ApNXub#oy*A3?NRV!Ax}iF7J$C4rD9$9WF-j}izEA-qix!u>uwdP0yh4!z_5}vv3XP@"
    "k&28XwntBj@((CUEtzngN)E_5sQ3k4NB4xrW659+*lFU|nE-vH2SP?u(sH!=#4MgT$8$qY{2!N*QEUh85D>;-1hFggKhP_|{tdqY"
    "y6hn9<T6@h*(#Yce5+Enmry_27dTH5j`Pw-l9@LGx;fciT_Qb>y`<ICVgq;Ww#bi&YO3V(9|3yTEjxJV0<H>Xtiu_<du%Xl_BDHi"
    "((%DP<+_)WY3pKrPl=wQSerkREga((k2Mn_IG5=o+th*Hckz0}D*9*w2OY2jnc_~^A3t^7edoz-^%Zgim~9EMe1=sG8Rd*w#{R=y"
    "&c1adpS30~XP?g)%ZnUKJnF5x4peor*|JlBnt913MI^dFz(@$v@GhB3=B{mz>(FtuhZ8hYR-+~;Q>icrP@jIxDW^kvQe<f~Zyzuy"
    "JUM^PzE2llA=|&T<BHM;P!IBLCW>pw9{`#(xyw=vK<(KhheasUX?uK;-=mBnke;gM+;_dHRDF!7VgimhHxy$Ko@P+q3yQ95A~t!Q"
    "0kbtq3>}KsSwjjFCcscsdC1XF29v3zLyT&29f*M@^`79N><ocLlJqpOoJ=wr9A<jQ3|&YIWt!<dCW3S6F_2OIDSA2)Idg`~==&=x"
    "xEMAY$l}Go@zAj`l2X4KcuQcG+}5beV_@*u7>eWR<L|1H-CK>wti$)kGpe(;`Jn_V<xg0x!Yidb<-)n^FrALEQ(@{Uy`~|8suJgq"
    "r$SX`Cto~)QlVW9Os!KP|1rtiti)C^%<^fgExXrfVL}&SHeI>JGa@(v!$ezi{M5DjV+Mxbpu~Ex5bjb+Z@{QJFS=o;C#7T|Rr;pT"
    ")uk?A$S%-E`(!9{$#_a<wpO@bpL)~7yK(|$-s#{7s(D|<#|>H(lEE{Ef_dgZ$#9PhPYt8<T#;<6;}Oylpt_~64dBsA;j9JYTf}dN"
    "Vmh8U26!on8{B@7_#wV{m%{pc=K{}!Pm8Yu-=iChCfi4W#ph3lq#-I1J&hDUpeRcY2YK?_6zhC3ZBNQ@zbg=^h@S?+9}(t1<(~6l"
    "1-)E1<X#akHzKQ^r(s_G<-_kEzYGO<7EUNri}WM$L$0qiSG+#neHvBlGu1$LU{_@I2}jPb(ZNg%i999#J4|x!t{8M&rB}^eI&6$E"
    "<?!;K_GSoILnzl6J&a0tAC8I{ESyLsucL&D<8Mmc2S1_1i#(W2@ZzX)WQqqGK`ktXKf<gq^)WoDzYy-9l_4%rB@HdHFQeg!pHyCa"
    "fLn;Nz<fj5?`0-s#@MlKct*FH6oS_*Gtu`M`sLM{CsQdu6<NrCQ#?m_kk6bd&k|WBqKd_vLZD8QGR&n_rApZ_N*2a3OVvUtS^J?k"
    "GnpW=5PAY5S6*nlin4o2eK_fKq3RH3vU{ScoYb~Snhj5i8Ct%+U7~Q1mG*3U4d#t=#sBUOJEBYwECW7V!G|;KnsUkq4|phABf2g`"
    "9E3OCgd`?31$gTVYOmpH^|lv0TRe527tm@)1h3!^5PWY%-~fsQ(AVmXdOWJ$=(%H%${zNB3m{9PVaJm9{={AiP@JZTV{pOaw=$)c"
    ";^t||mjVt|?#9J(jNqvwc=F(TcfCv7bd!=lm`cnkF2F1VL}@Ln#XU)G;Mq#5DaW|tXn>y%feLF<U;P~F*IjV>;M-errM2Br4fh<5"
    "5H-SDylm6m!WSv1AtJszt`zT5ku?)P1w-E<)MS{#d7qJ!w&5*9#6$JNeezJKeuQj%0Do{?_)4NWmPA$^_TOx~MoVae;<>)IgK%`E"
    "opmEp5PGA=w>?#+2EyUukHPC3%R{`0M7aXMzmZ{UEBd0aMIZ+W)<-u*gVg+|4%rjbq^XJ3c6^y^2U|{)JAgW9>?<w|rfO+*=%<J8"
    "34-cJ1BIGf+OpPccn?Jr$;hI1@`A`H+762!qlr7Gek3ZYf7mJo8U@5pLCg~b^|k@B>J^OgBO{C`jQOj|Gay4-%a}ex5i{|XTkYVT"
    "Bv?sZAFBfUL_tMws<T@rc2JS$YiX9#2nrW#pOJ=?rPdFZ!ZroxU8qG3M^fYJ<&nLs?c<t9TSes~9m4OFQ(k%QmFJuoa?!Jsr;<vA"
    "eDLJ?wM`xz4_AqEwMxDWqa*Ps;+O*3UpXh=m4{4AM9?xgNAC9bNc5*lb2MqWsakgQ8N8cBSKwY>3Z&W`T7cg+$w3;aYap@TrPWY5"
    "9d_!VB6>71;n+-<*j`iY(}Dh3<z;ggmHO0)hdQUs#n3=r&k=9BdY^)EB^@KZN5V4@PJyRT1k}RKdzAiO;T4vPgcWPmW0<%xT{h3q"
    "<_U&Jc2Z%j2d*^?Z5=FGnJ%Jdsx{!AyDhOWraJOY!*mO!d5tw-(<M_{z@oim2E9v_*DO-m8gvVge?)VF_@053o{*<ahQMqiDm@|W"
    "aH7NdW@P#@ABb5>A3nK082S~N=I(Yw19?3mE)Yn(YJnDK#K#SumQ}6qf^w~413OTQ7y+_O8aW<7?d}2Eds>R@!%)w08%Is19UXHj"
    "o>q(!?3jT4NkzKTA!}2x8_^I~#dLKcQLg-DJ07gn^W8B+{PvbfF*imT^dg&Vk-Buo7t&LVIbOsjEIHB5hFgI{@5u?~N!3{C=Ovpg"
    "Nu5?4649OT{K$$}lRZOH%K7c`oq7uO%Q2WrhNnK^Q{hy~lbAsppwq(Msg(Lwyu7TLSH01DQkDr_JG$dZW?VdtEM2f-42JMh3_L2A"
    "JX<xMFy{^YDh=Qgljkj9B6^1P_jMvk$tX&ql)Z1+K~>3^kIr;s3aW@j<I#(r65Gp&-I4vyeO~el!wws#P&DrGh|k9&)8j#UA?-!d"
    "bA|?Wsrt@LnPjx!oS{Go?6~)$Ap@1LLcMBPy!tsFEi^q9@j#qggfE#_Xc9fjUf;GdK|Q!oQrO99(G<sY0@b60e^)vwP^)6u359wS"
    "Nk<R0rRj@UhW6fSJO_Pcm&lN3N==D*VS=0@fGJuBj@IqMbH-EcS9m)9MI;@I=DeZ~7y`|Ac|#4Og?8y{iQG?!`30<;K{4gpW;&qg"
    "A4=gpeag<1w=aIq%gjaPhb&g?OP~==hlR3F0Kugs2fe*dDeAtPa6CZ*GYEJcQdfRUB*>h%RkTvsNkp*{q9>-@d#J16-HT?tZzesW"
    "7Fi@fs2aOyB1PTAP>{LOi(S70Ym^Z)8km694mWyu4^EL)^aSJa&`M6a`aZ`70+Lc_DvFp>g`GEgAcnS{?H%KQ8$e>G^G5K5l>&y6"
    "IOu%Tcf;(v1n;UX_r!#zLMUvV-kcUq>BZ~o$@YiUjUrQF6pzQM<-A`dRU^tb>6^-*ysedbOVLYH{ElMum`Wk0_fa{?debr05MpH_"
    "*jjGujItMGcMnRK7{t>J>53-OJVJtUf*q48o>ooqE|IKeER`y4xiAolid|Q5p%Jnwu&OtXm?EDd_3>Q4V(~j?ZjcLQGcWgbD$Q1K"
    ">cr<OyB}pZK*k{yMUl5;a`lMfWV$EbHrUqQraB?)>*irJk*-qyjqncZza`gqudwhtVCX@eO0xjaRldx}bWq<@rjm&{19)?@m1@mk"
    "o;puYQsoT>nc!mdK_H>}+iiA>(JXnk(g(5KdQX_Am{8h=#H#j(*O~+@$OPDA6f)yuWY`hb^<8&7k--~VAG=MFH+TFv$&sdg3$>LN"
    "h<pkl&XbgCHNQ4VR>cOYouom#y~}qy=qDZM-qY1ss#MR;RX(w$yS&+^kb2U~5{#EY?~vX{emp@C6XlK>T2F!nY-fp1JzYBEvGky2"
    "1c^a&->1(6ddzY3Q-^}5bt#~%7p<{9JCRz;weiP8{8Y4K*DUtl)hsIcHCN~qYNR|0-A$ed?$J@?kDm-G|1=Uhky!`24$f=3&mnU|"
    "?;?C=#6h24#O#C6ObUnNs>nSe+*gVN$-h|cs$Eu`z>DgeLXoC9&fND)#kIp6n`9i3U3I__jvMZYh1>T4z4v$}pgW!ae)D@=+BE}("
    "Nl)|~Nr59$%lk=Eo^}sMy=t$iqEDW=GgL$ZIBD5>af0Tj8T(*vNtV)8gKmA<4IguK<U4O5vdSb3Ud(QgGUIvU2y5LCA^4Tm?W59>"
    "bC9Gtc3r~^7VddJ$Ra`P2icGF(}ZN?`5h=~YCbn8IE-R}Q(8O|AsJMTi1ev)U)2y27QM&}<pm(+Ur>jm_%bM1Cs0%ydVQhX^d>N>"
    "{hm63o%^LAMFXvhVShU5KO$pbC`aBjsx;eOJ%ZfUfO!bA8>cpUMLO(;5;uv3P1VZ0S>;>hPVrPIp3HmhNl$c{^QV*MW*xLyT~k+Y"
    "wz_|1b)|=c^^qyl*UHbU68#{P9h<euUxjdoR`T@Jz4(2CybX*9tYcz2ne2VrY5sRvU)J{-Ri$(y{SK3g)1BysbLJ4cmqEeziIBb{"
    "^dBtS>wP0B%&i<ss0A`SbyIXk7I$h-&sa9kc>t_PoFZ3Bk8DwpqAyc4Suq#3^Hhf=MijG`RE;IB7jNE8S$I<L)bEt(rHQ$;oe-nb"
    "uzmp7s^YH7!4}{gKn0Vrn91W#qZCeNbjkV}xv|DV$6u5*{^apAma}2F#Nu`L!%-(z<ZCdCU3v6{LL~dA4o#ht;MGZ|W=&$Iksysg"
    "10Q89+dxBnN*nzyjX~_44}#cHJxeKG2G`*nQ)0qN!>q@S`dsg%h-_P>eUYfQ>2pIgIeAJnze|1i%)a6KJ}sK8=MMLs*MWJ4L{H*K"
    "VuOc5H06NE%m(pvUdTcoM$vBp6po=VO`$fN4k$_|hSC&Fc(!mHpmM~H9qN4jNDAJ82O8+9W0_3_>@09nAKT%3vmt9kG|d~U(ly8*"
    "pcIb0QNsM(5o1G`o`CPcvOHr-0>X75RO>!Q36s40hqkQzK=hwR?<aQ|Q@k1b2{jp}rw5mw`5zOM<0yW3t0bDJg=S=#P6855lV)T="
    "IePYXst*Z*T9LigGIRRK-Mzvet2&YfA+)D1{!6I!*}iDj-BuN8w^U6C!)-8B=-8tuvp3aVzA}YJ3u-?t=$Df^(+)t(S~jJFBL^{x"
    "j&-hRH-ozDUwA5L)DSs^8Ij~vj~X~uo^U3H)9CP@VrZlvx1E>AuCKKh^SH1%<d)h-sPYyWk(PG;UbG%-t2Nu$8fKcKM_|y4$#ALt"
    "{Iv|Lad%<_0!aj)bOcN1u2nVBPK%5^38>#^M9v}*0}VL+REz3TUnb&4YZ^UpPEKTEiz8$R&AxNk$nupL!pCmdvFS-fxQsLgl;Hw~"
    "lyxfXChrrK#@9Duv#x&o@bUAXKfHVUVg2v#KD_zs+xP$RWf1Y6gB|oeKw^o)&!0bDfBMV&>rbE7pFY2Sca3cM#>aIOTXcf1-dAPz"
    "Wl&%=2YH#NboJ)-`w#EmzIpv_{kOOO@$vQNw;$fG-@bqI;cvgayZ(Ir#SU_e;;l^Yg@)JfPc-lT01qXEY?x+|^<2bOrT8K)z=G%*"
    "aOPz|=)ye%GlNOpZH8yTof|=bhcjnkX21Y0)U)8u4H=Zxq@d`h55IqWbG?55;qzL8$d{oJJp*3QUsc9FzyAFDr}gje|N8#Jf4+YP"
    "_^7bF5N&+;1LtR7q_?ONfJH}NR1}n99MppN3Z<FKoze45^A=UTNH02Yo|geRCWA^lMD3a#EkyPM!SC>D_d|Nbz&WJ1@qvO&6{8RB"
    "V1?|gA?xeEmz5q`z=VBGxuI+k5JVTrw)X7^B^Ins(KsDmDJ$N1ay}G!*pc$@upl$k`ILCxKmxOZ2D6_gu3hfx*X;4MV0F0H=0YnH"
    "hlX!d9xXn!#Eyja9XOT90-arsRJ>pa@_uVbn-b#UTaZ%OHGq<7`h<Auty0zbEr$PPel=wX5AA$2;`eGMzT!Bcd>rjmi^;`eN|tXy"
    "i2K5G3UNF+8`6gE-w&@?-+Ri^)7?(1h`mdTwo&>I;i*&;b<BmD3+>pqAB_@grD0ogGWr;9fqTf^L$RaDJQRjzq{}T0V&;R2`pb;H"
    "c`cdmhi;&8JgC<uUXHz5BisL0>TGW3*;tWT%h*{SvkkQr)@na&EcZho8plB2CyGWj$rMO&F#FNKS|#;FHfZZbT4|34d_Fg>_*_CM"
    "pI+*^x=%iu#)Lkay3V#~vF!?~@8&){C3_6|es4h{U2SILWa52xguHVO!TOlCceD*^-o7%XTQtBAw64%zJc$c2BLP5B<U|b)p733<"
    "zd&$Pw#{%>8azzf1(=)A2S<sXJ}+1!dm}x2Ux)*9a2U%0-OG&pYGnZr#!~Mu6~;l7SOA26_$K&_+2>Roonkp6cdgR>q2#iJu>}mE"
    ";mLfpd?bRzqjRgrf*^CT=NN+tc~C;bNkOM0rKbu+3~~7cdcF;rXP1gLgPaNf0S<E^XR%}rPv29`MH(NCcYyC$5c-}CeMr560o=Wh"
    "^UC&`y&41-#2$v2<mE1B@3vE}{!RBN72#dgZHjC#NI42eNjwid41kwyCl;KsrB^NCS_VppIE#(=y}oC!uQd$&p(=Eqt^)Yf3MLc!"
    "C}qX+-;(oOx$yNDuczArfhiXQ!jK6}Dk1iON{_fR{e|P{aROuV{0pMX`Y=-!nc!o>JzehODWrv~K*bxRp|&mJzuA@PJ!6Yc^3WP`"
    "P9(0^M3ai6O>z{|&Qi%l#(sfjLwR~+el>*yCiF#LL({btJ7{&^$fmD>fI*VV@jQ|E*d}#$r<@dF^aG9An*w8q9|B)b-JB$Zm>nrL"
    "wtok7?5W~TZ~@7jJewJsv=%VhJKsax)&?5zA8w13-FN$(Pa5fe6Fd9Cy8zuV%}ekk0(ofDVU7aggkg7G-Mz%8)3Mu+Yrwts%^O(p"
    "3o$ZB1Azck(V>ANCQ9Xd&2fVAo68!I9d_a~#s&UE*@b%uM|GMv8MG6ZmnPB;EYTHyD7yA#vB{wAm<~vuIUMjGiYoy9h%nr*%mz8d"
    "5*<_OW+$C~zGn^#k_+_6Y>T|_Y*6vs1yFu4W3MYuM8R_{Ai@a?wDb9v60&5Hqvu(ZdQOZ_(}IzKN9;yjKORvD%^Q{QDv7K~{E2!0"
    "#4&RnYZ6Y=+>`+nOuwXim~=3u#>$3BE6b3&T(eHqmCEyl7>GwHFef`Wp*_6w+-e?)+2cHF(s@a{I&t*<NT>&19l2bpUxfe+4Ddx1"
    "mG^0X({fIRQMhqLWvj402&9D8fQi0g2o%qEkHc_%G>YDit?_ZqUSCO_h+vUYnI8t?$3D7EVc0WtW?bKPSWm5_M&5`tUgt`v49*p%"
    "sq|6S^{4l*fBW>851&s$^5L_v;!0Lr$QSwb?XRytU$1}r`1Z}U%%IUjZO_ApC;9NM-oO647UA_LpRG%&`;xlPcHz@sH1ff-lMf{M"
    ";PfR>C|+eq74YO)7z!mr;g!hnT7UTX>-ER=>o=d@{{8xknap3xM<n@(u0Fo~^w;&r>rel9|AzhNfBgRT<Mpp!OrhX1He$&}eD&t{"
    "j~}n!e_p@)@cGmFZ*M>S?e*t3fB7;GZmc99t1Cu-{psz0U#mR0GXO>+c}VzPy!nUDg8^6wB~gSCWGqbv93IU8LEbS#6KR99ch_lA"
    "U@uE@sO-A!xj^1HlDDYuazkwQ&kn*fV7T5@qMANEnn|@9Vdq{*yfLA;54<saQB<m_t!laiLv=pJ9KXM1)8+Dze$xv!8nG^%qp3~("
    "nl5!b5<@y%@5jBn%Sk7ZDuV1|Y9e3MH9&0%k$$Vn>hLJqE%MsAUb{7uC88(u{N<P&7*&@wH~&|`ezZ&fpQ{5qkUwp*pYrl#RL007"
    "F`lA(lI-?86h(K2tMnnQvy()&<avnJUH0=&Shx36RsU3W1xA|;WNzT@6-~L&pTT`gLP`G)a84IVs7*et6vhh7Cles1izkm3z~B)s"
    "sJykD;(8y3H-yNVi7Lg<?$89{u6!H{r&M2Xr#jz!%sP=FwP7awIO4K<2(*?vYB@#fbxf3*At4ab(Xu2zG6nKK{;_5n<Mqc+e|h_x"
    "C4G7nb8gR^t1$Qa)2Hjt>o@P%e=VumP5v*q((keY+<g2%qHnLCf}z|@Cc;nUn7C%O6f@PIKV8K8Sqv~Bpn>@;s5vy?``|i5{{Vao"
    "jhSFKk7aw8fiR<c3_N!<L_-!!?(=Wp>}YP3v&R1S%gF%587R9~?0=3Uh)<XCDa5@i|0?Q<KPlsX#OZpQuXlNRTULOTjpAn6VCg1|"
    "a6{Mdfq`lIl^r8+=m5~Z`g!H)g=@)}=Wi$o3%)^i7eME95godPM&hr2PJCV5Mm>F76@};@i-X=pjJ-(NqoY7;%;-B~9^J<XppJVF"
    "IjQ<M@kCD+Nv1vgcn6v>Ra8mW7W%Lu(Ky2_?>lHFu7`=^G1P?rA4e|ojcUH;M^YwJ7*2z8>8Xv8C?<1*Akidh6ipEWP&ZF`>#-MV"
    "0uzmUHcXIysOqo#0-}2@Ojd4{cjL#pM2vcRnYHj}vOD>lQk;I3mG^mFm89&iR_=76z}5gFv0HRZh2js;7mD<F#GS+OGg@0my(RHj"
    "nhgv{DR{d-YN1!ky5&2zSSajGqUj<7Qpc0@<;##-VAE~*n9>`0`cHJw<T=QJ1P$l(jyuekQi6hwlZ)tCD9ICaa`i;dLXI-3POh3!"
    "#4uofo40)`JRb497ET@r1P2Cp4R44{aSFK{p6kb5F47zb{BXK3aY|I0BUZz+e6~fJA7GusB!_XrB#T{|?r=zJ+N^&v;cW+<FOw*q"
    "E<++au1{CRP$<+s6ZX-~B+#5=6-CQ~D4s4-%pxsus48}iC{Ef&<m^|l+GQ0Q+(Nt~c?q2f#g5%veUo1>lb*9r|K%6_MMc9hlbW+c"
    "&XSlcH)lz_`O{e<fmI>{3jgzemRDEw=KpVe?9e?jPK2H!X?FEU-Qx&_f*H<&!JZNzOD$v>Q~DvhR)-eRsPp>`Em_&lhsonN;rx)w"
    ")Xks4t;5!nxCR%`MU+0*9VY3n;^{(zCJve#vfDVSz&*6+dUg={KNcR2$4VW9Xi1^Ac@Q7+DJ<Nbg8o&e5#QEniJglNee*!gKXLx>"
    "jU$a?8A$cx*Tn&{BV@@)?;^X=n=i1F<b~1I%J82nXXxMfUt(46Fc#^%v#v_&095IB_2Khhu0Qg<juzYN^IXAR=_N?3S-C2*9dtq$"
    "_z3SmG5m+`wDZ5zn%tADi+G7OiO!%`A>U>11##qlSr(%qFi2TKkaKAHI0yf$Eo*6%#y-BnTZGrpmA5!ZpJmEDNhX;W&rtojy8f@%"
    "Z=mVMr@yRUfBf6^uZzR%ci3`I%(%(8#AsGnyjC{Up`WTK#mrxx-zZW$Z#<@tUOCB?mwa};KRFKjzhAE0|30z!mgL9v{2+|tRq}6M"
    "!2Uio6{jC}o4h&yJ+v;D<m<%jbeG~Ka(;36Km1rl3v7|>1!M)0MiS>nr)vjyN~#h+*4Yu<Xj{=*Q0Y~HgXz?A5guV5Oei)HYK@1}"
    "YB3sXDanR*ums|Yu=l)`Co{cqm=R=*b1NT*o`A~~b|f$1V{2%kOi`8vA=K(c;R%?Ko<UU_P%Hs4q&D$yj?eynCBL)!?7745SOgU_"
    "_D=!tzptMD*d7E~<@_nO8Ae%zC|hiQhhu@N|7}mxvcvItejSxxOHgRYZ#ZDh9ZKXG_W2!V1x2aMX5Zn=NQh`-I*h_PhAuRg-9LrN"
    "pTa-~h{Ul2uM_w=zdick%;^$D3?ZgBYxaZufGrP>-2d4?YA58%DzO;eRo4yTi&2xRbE?CUT`y%j$o!DA1Bm3Ns$h<J`Pl!0a0p`a"
    "3w<(@?>#43%x7vJkEmO?t~MwBNw77gjK9xIrir#bEh%|!+M)p&5)bs2AJ0jgY6B@|y>fNe*6KOw_SL?}jGA9C4#G%RtLEVVM|j{_"
    "_hU`UpYy~qUAs=#`-}*$bkEXr5@saV>4A%P5{9!$HxS<5<;9Na?_Vf;Dw=W4q7)T_a{g@64W#>m1Rx%Tvq|?}<}2RAIg6cWHUS6H"
    "e{?CvI=(=2hh#R%2GXK;QR0NNi8Rus#1NqDN3+T_qF953li6fiKiRQ6n?#G`7Xr}4UCk!Yf^xs#kvE$_msNjXHYm~IY!WRAud}o+"
    "P^d$9HlgmR?~5vh<F-oN*+g5Rj<y86_Q07<zI*Z#*TVVK@*{Yo__4?HKA&FBNJAGV?9>L+Q&Dm7z1f5ve1U{*GwS5W^btF@N-ZsR"
    "oY|yY5HGV9B|Ml-!Ug3fzs1+=&ZmqEMFq-jn=dEz5A)EjDfcmnqS+*y*?t-Hgc;nX!F>1lkON3~&9|hv%_ru93x#^%&nE9OnYfpC"
    "cQ$#a`IU-Y76j33BF~3s?oiyF*~Fd5_So*nvq?KAUGq~GI<v_-Q3kIv5?;+F;zSsd<Qr}l%_Y`dS!V_MP`6ZWVntQo79KY1=55@d"
    "sv26FHx!|3`EceUeQa*hBlvUGhIYSDLrCQ(=<#E(!&u<Q^MzR+G;@OU1qHhRK(PhI-UdH?(^S}|FtVWW+h9xi3GfvKfjNLsfSDZr"
    "**^b<75j&*In$oJs%aDlWtm@r^{*c&&ktjBU@_xJ-y<({JafdE&Jh$<0UNGw!LdJ`4Z^~~%kl!(0xB8uG(6I)zycyo;0ZEI&YWo1"
    "buFkRMhp!Ov>*Bwz-TH%{dta-fga@I{Bd@ZWw1wKY51;!&<glaO|asWpm8$TnFre)!o7i=7%0u32C<4q$)X@P31cfLWXKQC;_}!#"
    "G6$eN4R^`X9bTj&fhy;tTOYA)q|$L66gDbp_bezs`>w#&kFwJ&b=AUZNdOa26tS{Z=5s+O5>SLab6Cks`7SC^!$gDY8%7YxQT`ew"
    "%ir)MvM(nU>(dVASn@bS8l8IBb^xti3n((34VtOPoe!aBjvixK5q)aDoK)`4vlWMm{RmnIvG3e6q-zMb632qAG?teK=tqEgGI%fo"
    "b7W8(@-j599!eVcP;jGd%djeMg{*>}?^=jcR>f|8o0S<ewVAN{GNpBoO@c8ZEPw0O&w&k#2P^jpf~k=^ZSRrcT@Ib~(uaP~2mvy*"
    ">}{~cg9RMmaNo~Ur&lcv<-QpgH<oYHyu1-#!@PhZPqDr?)Q1tw$xk2a(6}+iglXL1E8S%vK4gJfKnDX(r~v_R5)8%^B=d11yrsg6"
    "<p!Yz{h@c(@RG6vGyM;IZ~%x=x~q<2az)m*1${{B;SJg40_k>Zt{*e2!9wW7j5Xftf~G!jzQWjo+F{1luB)+oM2bB>Q}n?mfjJ8C"
    ">3Z@L?2LijkeZHV-5i%eSu3|WlXDdK79xCjwk<ehv8EkizMrv2t-NZ*c;Fc_(DD_;do?nuiv0%o0HPw(Je1q7JA60zl}&b+L&LUG"
    "^k`%kAn%teU)GWF1Y>X>Fx@jAWIs-4MYgmhWM#`&`@w$Au+tchWq`<oW|;gq$@IHa`MgbNVIr5vZeBc$_)BHhVT(k%D}l5IA_k;s"
    "u!k6ShF@#$V5ACrD@^Uk%S;*uV4NHnreV9<8|WMd!s+QXCCU7p02_g;H(6MK4cd<=)FZLp{ET=ZGLq=HFr5)l#me-4eV5<ftshco"
    "=#|!AWy2fbsTaj4)+4h1bh4;@Y6BdYICf>z9U<|bCMG<V+ncliNPZ;5JSm3tW?-P)-ig+Im=f(-!i9@D;A0<vjbVlB9OTu0Xr3iF"
    "xR|(XiFH!6Is@^tDwUj3&ywh0OulOGu%7_@<)q|(2Kwxh%&Xl=Rs0M>&lEHDz|So4O}F{VG=%NfC4^n1;M)>&rGA~7VZ(Qz9EV*#"
    "qREgwxmKtNQ>w3&7jqS#sml&I$}Qr=l8d>kik(Hwxoj!tJMzeVwFN*X%*cJQgGiUL@*MqhlwFNx+|}n;ihL)rZN&QVLX>Vh%8ojV"
    "G9ve5PE38KrZ@|M>NBlo5mfEV6#77AdskQ8?VVPQ?EJ~`^kV9zpx1!oMezA-lcI$H_AD=pAmn%25iJCh-{FSuj5`i1)ilE1(a7gK"
    "3)e-r!IE>~y;$$->d;4>U?wNdi@<7E72JNj6=%sy_puE4D-o}Z+7-i3iY#SZ-rQz1yh-HQ>&1EgTr9|m4OI23!OTP^`pz^oX;TaU"
    "AG+BaJ_H&DRf*dZ`U~j!dch<n_H2Qe1c4_3c){t?FnEX@?82_csb4ss6`loNamK=+9(US4&<rvBd{<XT(K-kn@J$MSZ!rjnI4Uc2"
    "ze6UI7SH;z&;KlW(WywDbKXM-;#^zaMx7nN`Zia$h1sO=!LCjpZYT*~DR*+6NP~=p87nb<6l+nfiLz^tCmLN9+5i-DrsT}w{vEC&"
    "3pituv#!B#TWkS%%(>zpf}ez#G6y0HOyPI{2BGZU-2huF3*cnV7Pd*-R8>JeYF+|hAxqFA4OA7{sh0p(Wp<l#7jhK52(W_RR3IUK"
    "5nu(s$>DRAya<r0E6|m4szqGA2!z7gAt83I6TAq7BBn$!dJ?|~d?Kb)GzeY<NOk4c5|27%<qSgoOL6s3S1{8_HoJNuKt*h6tu>;T"
    "fmXy>hXtEf;i{kG%X@xurqq#|>>)S8+U1a$_!jZ$QXZrhS5(vwA+(4`qnS|qGFXJ5(R_4AN=#hYG~+JiqR;2*6BZJiX4`m1NNtUL"
    "i-<Cw32tY27Qy3ECLrfAzlAB8S+V1|zgjH-8Pu?)Sqd#Uc`RAxoeh&<0l;8-%8uhiyZ}yc@(j2S8^npkTL32n7wl%0m^nX4$T6I~"
    ";;{rw(8NyOZ6#hUfDwnP%pRrrvjjL02h|1~_)8%K@lZobEZ1t5zz5B<v04BjhJK9IY5{-{UJHJG+ci{1GeOt)BH&ORMSLWP7eEHp"
    "39*QwL3vH8AssFN4VvR)5m?Z@9IoRmfDFU~sgQ}g04kh#RbU_H$AuGjg$-cfbs89lV^#xHZ2ZyTI9>QJ6oFA5ezWdMULOX1nk`@g"
    "v((ti7?F<hK%p!K3Qr%KzFfP`^l)LFVf7~TZ-If|UT7n*BdUL8{Tq+javuqgtjmBhN}@Ry(6tZc@qEC?4jp6_R_wBMW35a}qQ@YB"
    "eJ8CSZy@c2=k>rj^dbwGFp(2ZKU!!fT(%XivK4M@0U4&TLQM1%3~*q8YXKpSb+zlZG={6laqJD2yQR>t2r{c1CM28cn*arrnci_Y"
    "$S|2Gr;LfVTJJLIm4qd9R1>;S`?g6{?=ifg>Gf#%g8~t`7QkXO6MN{j!Cq8)OD**+;KXPi@GA{_TUf&b!eR~^IItZ*Zo2wDhw0!-"
    "n=|2!lRQ9o4;y*|mg}Q~m})3R?g$(>$Yv6??r?kLb>DRyzXd>T!=z{qY@^wJBQUS`Rn5p#xyZIE_xWuHO;7sc&z^L|T)sn9az0$w"
    "hE>7za>YR|GOwV;0X8{dwu49<WT&P1+Ez!Xgn-N#I0ZK1vkz>~Gx1PHB;7&r9Dr2O?Zo_KOgFlrx?|ijTOp1S)UnBjARgFI8%7XU"
    ")4USras((sJm8ydcbloB(6{Vt^PE5Eqlkxplb7gH+SU2K#ad@M)%><g>)lBjYcK<4=qG}B^dLA;5g?q612SCnRMgql&+tJmKfW{<"
    "))E6~KAvQo&Oy0rx9XeM6##c82Kuen@BS(Dw?%{r@rPrFRs7jQUph}F0%hM)YYevKfnKc~Os1Lj7%$sKvFnvFu!ozCPmrSD1qXp|"
    "Ea%iii^n)`A%a6)YqodU_G`0-OCC~Z;bH?1x??QX?6U0k`+SSKW&Z^VqcN3+T37JIZdCabrb?NL^Ej$5Is+498CJvJ^wmbu-%x0j"
    "!B^&zdl5ZKpcJVs#=<&+CJrF7BB3LQN=K5hh>jsjp-3w?@d&cqo9|f=(jU`gh+yTWy@-}ffFK90H;ZWYQEk~5Y8DX{fox@d)I4&k"
    "B>>GMrG1ci9sw2c*qpC%4(XK9SJ7?Xokc(eS@xZid92U1)G{p<n4d#P)hOaT;)xV%@Y~KNm`ZB%JYtD03@9;gHqnG=N0a|6o9)(Y"
    "EAXvl$Jr!O&nlipAP3R3Swxa(78xNec<-!ew>)knN|t%#Y6F9!zKL8?_`UQ#&5LxC7kT>#t=$r15#^MRbye+qI?^T-hA1=|+vWRx"
    "hDCV|GocPCM6-QkktO$I0cCzQ)u?c@0k5(KJFb=u(~ZalpR9}p_K=n-mD`i8+pmj9>tMOW1Nhs`1y(W2gws{Dsq2?2X4XRSp1COs"
    "KcjbwA-+Ri=7;V;CVmRFlFgCrs_10hKHhj&bcHw0)ylD={7z@VyK<~3!qYkMuiWg)Nvz2BXfAxgg?kk{!zK`uWbByc;LI1wx5z+G"
    "i#7Xe1s`D(@2X^A*FsD-@Iqt3=0v-agI1BLgpnY~0z`ma&seBAHH@byWTdMJy&?QJAF)5>Dh!P!`v{Ttl83LjrHX;AO}B@R6UU4a"
    "(yUmn7Z9s>n}2yRMbE!L7{V^c6&TB`EUs5t-tU>nw7h>VQ%|$Dezc(w*V%#5V$e$`LZob9jsHH~wpIN|6YZsg7Gn@lnj0F6B&BHZ"
    "4jD2-BRy>L+pg-GHPbsvDma%_<u`wR{r27UuUEd~07zebyng-bKQMpBTv(^D^Y;Dv&+p#;$6r1ZNAKA|zj|~1{`1Gz@7BM4{P6d;"
    "zg~Y_fB5_L$9J!PTmSz4ukSzn=X;#8VJ_`c*hT)djq}{d7y+hm@;`pR{$0GsP+`Xlj4{H1ofWX7a^oALhZ#369jUkPKmGpaKfitR"
    "mL0A2>py>Hhw9_?r|XY@fBpIGhxebpoOV0#W@7cz@BjX{x1T>VZbzJc-v)4*@H$FZ-6&cF>VAL!=KAC3*KgmCWY>v}p^%bQ(ejW@"
    "4rL=b;9TYvV$KlLpi(GGVG4y483S9B6=ZMmL<?dDsIr?FRT%8eBEn>=*xb!0Z9TBF+PZ^_uh*NHidH>nMi?e*WDOs6fC{P;8v~aa"
    "7cQbeYz$atTzI$_D`PA&<HDy&R>mlUQ%l5`crX#$Nn;Hbvt9BA+J3sRxqZ@f8?LwxGNX9<iO6P!=q+&q$w%hC2a0l2A63XryoJW8"
    "JH=un_3!SGZqN7VOb!1zX(Sgs_bM0y@1X%(e8uip$NlWOe+m=!e@X*3D^AKWOmY3&{?3|$(wIdIdyT$+kKIPCSYc!Mz{VU@3YM1D"
    "6+(j@cgh$p=m&C7Y`#=y#%0L#ec~TC)3@T18OH^85vgnhRstqt*uWS@RbF{^Q0t-6%N4kSn5El0wecP(j5t-#7?SRNsn4MTs`%0|"
    ">8i<&X^mq{zyr`bk)Y#MVvHpU_1?V?sm7ZOykPJG(Alr(RiNEP_7;5!SxgV|V`HosN!G)fu-I6(si+4K7!~zCkD2Zk5zQDA+Y~v+"
    "P^_fS%)agNwVW-0R*am@!jv`$Biy7N%wju&;K1Ln<jl&IuDp29$3R;hm}G6qZFa?nR6^v)zwkgC(ce~8y%X~^p?_!BT>bX(EwkOP"
    "*Kc0GfBg|1SOZV&vvIS_n<Ef`P#?_8ymHaR)#sr_&qcle_^FME=GX`-`4WCjx==i1Ko@;~Gy_`(-w<lx{?j_+*_N^_OpKi637^>c"
    "6~-69z${JvC(z~<XTn{}jg}HnNcxqxkJ}=<A+EXuB=PGW4(~b1vIF?_3zaQ+bJ1jf-RCra(l-?P5md3u?;$-Nd4sr>p@hS2!gQ0I"
    "juksfM;K8D6-oU}G%*otmlZivRvzW(y_>(i{`lAH&+E6pzPm;U?14CN429o}F?7p)`t<h0`}N1`|M&V0IOu4Qc+X*EhzZs>?45G@"
    "9{MX&&@kr^U7N19YtD<{Nq?J3XbLu#*upo6Ri}<?h(&!>R=K0<F&Zu|Z(F*8KqG6qn%xXAmkbpn9^>mrd54}MLYea-G^E%uL@aRy"
    "0$76IJELkt1V)(JTg%k^^07xxaPMM$l^H4t;#f^sf~8fpa5#9eci$;`Sadvnog$q^q*2KmpD$D1^f#L#E#VrBJVV?VPg>mjpeWJZ"
    "?<~bZ-)k=L3_)Q67pEQX!q5;047lLESsX*@AJ0Lb2cPrv5G=;QKO3g5f5l{xD6k~|m-B(C&3qTa*&zYyuz2!l6wHnaKwadKM)B;B"
    "0OXZ4=mp>aIFN1J{9tw<0Qx*<De`6qfgS@RZ+7_?r3@r_@$7Q1HiwG**#*C0TF*AE*UjUh$Vn@3XP1A4csRTCi*C-?;seA#m|gt2"
    "XI~(&@6QeZyi4=$IRJoszaqc-`x3|<CCzi_0hjVnLpJyUZh_dh3<W=8fiGWSAxbR6!H-zrq}8rmD@$O|JeKWU299tT>4Rnw<{B`|"
    "8B%$eO$14$q32tY|F&9F=N=%3ql7TYp=tP>9-2rEbwM>lK=4AxreaT7vc2b*DV1qa-7+H><Xf3skH%-gz6=xN$rp{89L5F!alma$"
    "K8wD0OJMK^b3DrS=FPi!EfTuk{N?rg|G54YT}gqd@Q>x;!~4(I|MhwO`}@~_fBp8|>wkZD&0Si-tbF|X_S0{#Kfn3Q`nT)%zrKC{"
    "A3XQY5CTRp!5F}CYPfn+pTM9A+&bNZmnz)}0pN~f;gtcBroMn7N#1sj93LWmNiqNqAceZ?8H&BEgQW(0Ozts>Ny;3b@Cm)pGCr&s"
    "gQAbev}rIP>8uRpzv|j;#lVx2!PfZ!=H@}w1+0}>cUcYXFb?UF0m6}CU2H1uc&4D02yYCu=EKS0k0N`2TU1c=A|W1`q0OL%ux8SZ"
    "9yQ?vrQ;1h+umgjB}%5peEm=M1X#Lw=SBK>BePGh&iS{$5FQ81@P>P~yHa8n+%;+j29x;_QS+-a!3yNe6fGumCi4cbq+^>$QO&TV"
    "$qs1=x&oBcGoBb7CdO^245)-C*g$=;;5+C_>e7|<=!Bv~a<r4d(?MAOMss2Fu|8&KGL@6;+crD$YM?kWM3|{e2$PC*8@$m>L!VPw"
    "5#wiC-g&|g3}MBL5k54*4Z_ROq^t275t+ag`_)qpwp9Un>soWCV-5LK8KnB~EBTn04WQc=W8khA>IvI3G9mj%(BxPbIx5B&W?ETh"
    "rAZIqd={~FWG9OCxWtU?fc?de4}|Q4SkIF3=i`D7E47Ue13%Gl#Douwz8tH(RP)jpdRKb@^v8AfN0(wkAWbp2fnI6%G>Ru41Sg{z"
    "*<jY|7TYc+x?{qK2e`awlJmNS>KoX3k*fxsKI@v7qEOS(0Q$bFpb?uA=bI~;sDOp_Ez?j3NkR`yqsAoADD<pB26P~XWKXWC$|HHG"
    "cKQC1|Jn_G>lZR$<lO63>?GCzV*(?`x;iks1`}%rX_K>Y!qp$SCs7nS*6?BqC;g@b?qJS>2`kyRbPNAJk~GxWEt8M>QI+8w8>Qm4"
    ">crMSLfUK!rCrvdCr$Ki<-F7kTA@D)#Pj_mv4#<{T^C8HIDClNJ7HCGF@8j&DRQh~L_f6_k|&4-)VopVp`w2FI|Zb{R7XndjaGhQ"
    "4IyZ%c(aAMxonN~j_o`9PW6Bjzsrj1EFhcLaYAcw!02E`zBHk`7WTNQiu;ToM)n5w`8OOgd?U_)22nEK$aBLuw1y8>SnCsP2AP-s"
    "oSd;w(46DE(fjSww4%VV1|XJr<NM|@3Acwa(|O~dJJ>kS@dD2p{Mh17Y@O#>RAELkN00d>Jwj*m?+L3rh`>fKf<Jlk{Ajfl%$;M8"
    ">8n|-Y@Zi7?owFy1b-6n+`wN7@O1Vx)vX7;WVKof^q%6cm);;+3i!_QH#~oiXVE;f$Y8&L11IvF@W(9JUo`zufj!?#7K6Q0zT|c}"
    "!E$idvn5L226u&%3}V$Ufja?pQRJ?yK^>fEjG;mM3ydE@!}$9gwR4DI|52cOAYlJk!!JRU8bDILSi>!X6!W<ul9%D27}ySQ?Z&ZV"
    "4WEv=w7O_Q&YA6|a3UIC@T~LtHdVMqfi+;Vz!~8Za+bKhHAphzZ0OE#S3w+ELnRxmDZ3U~L?#@?wp};f;gHskvdfxC#SsB*a0pve"
    "uWD+Y0|5*4^-8A#dLjEl-Nd!%Rg(UaW7>iT0rPLrxd;yE`~aEQxo|$+h~PbB#KkLn4yAEi^vS95DoL#2%Qz<B!vdS)GbZfmIOCY;"
    "vou%E%AO+AGr?mK!kNagpXS5y-zFLSKAw3&Y%i!~#Mud+c~0nC0}UIjDZA#op+#hYHp+eOsq8_b1o3aE%py!8-y*nNKo@iG5Kw}|"
    "BA{GA8S=a_Chyw>kxK~oDXrnkCU#svTi&n7N9r(<ScDDXY@#a*V5`MOc6t9>h-EdOK?ITA#3npca%dA5Mi4|j?~D4oU}kaO`@#(v"
    "hTB2{=;P9K+ik|KDW;a=GqZ;Cg|FyqmK`Cwo8$aMaKYIUb(qx%OGn#9-SYJz1$AB2sbecMn0*~y9?_=k-6G#-SkSJH@S;Xw82y06"
    "T9HoKbVq2H+wc(Xw{6iut|3*oUde8$*geO)a0hI2q2@{H;p})uF4ROj-CH~l7g+qPzYrA`R`W5Tz7O5?JL691uOq%NJ?Z;Q$j+Yq"
    "!0Bv9>7t|^;7IOAZ0<zOBdBMDECY}9bliEl5Izh?PspDnrCX>gfTC}HOUaF2SNk;$TU92kVk1<MD$NV~^KUR_!5lBPb=5TTe8?}5"
    "&B=!iLdqPq<g$Yfm(XO3JiwejJ4mo&cip|-Q4AAVx}(fWZg`^HO?rD<L(ye+HCVSX6inkn+lgq-ojBIjKPfS5xE01&Fp&*yFN~pp"
    "KImb9*r+%HY_kK+{-A_oFt&?~N_GG&Zu*$8IUbmC!jWl^R%?Y3HJg<BtkxM%=Z7i4UDH;(#~a9U-d9CleWBsc#t1Qu4LqQjOzW6K"
    "#xxdqPg*oyV{^oq$ON<!fgESrjL;l4@P68nxzOT3jXS1ZLp__!z@o2Y{Prr8spRHpLDRMSx;e<ak^3NW0*94zNobA`SP#gkZ8HW("
    "yelOzfO(PN^k<*#iVPXTf<EXFiJ80jNYEB6phF4$3bA4fM%R|xoM{fcnnxQ7e3h5`E7)`$u3d9j7|(%*)W&!)ihh~i5AUmBb_TTd"
    "8{@#G<j)@f3u?n?2DJ4X1Hc&aXI{TEH+^mOv&ldEY+3P_3@ju4Kz>Q#7xeAQKD+mCabkH-9On%+(5dVm?%Cdgg4Ey=4S2Nlp@*k!"
    "Brs&hE-p-CX3w;=yE1DU>o{S`kFq<iIc`kj=8)gk>8a6Y0Y*&uv5P9xxMA`win^`ij5$Xy0eOHQ(Y5M@ppVAVOF$rC2>M#R5CD?w"
    "yafCaZ3CJYf<HY!F9Cn*Zu7$MN9E`x;1BTg!th69=_TL~F!X}(N3!!0@Q0rILcoVJV;OnSMW+o}$&`BItWF^w*jCsVhx^^6yW0lN"
    "OrbBbugzhF2H4!e2r){75K|DcZ`)2pLiJz93@&r@;7bO3655M_^AKIPV(~CIkbYUV%eZmR480ELBktM3g<)s|M_!WywV|3#b|!=m"
    "c?bO$<SfG|F-HP&$%?0+ju(O)!(G)uP6?myZb9Pp)^dCB)+_zxa?xZC6KgZ59esTDv+r7vZ-ExlNiV0$!v&<S+ln|#a$Sitp#@bO"
    "NZ5$At!XwU_UC!?`u&^hckiexPvD!2^mrC%W-=V^&@~tAu6*inVO+aSbI59jx6SLLMTo#$v_)T=K8-*LrLSdL<*{pr^Rb^KhxHvI"
    "&Y}c^{^;`iw19nn$ZHJqWPDr2N#p$);NR7O2|&eAmLIq<(eo!JaezM;_2qPr3eq^en?Rhnqka`cdr$~6g*N5v_2DNuN&st47`oD1"
    "+7`a3|8QMBvvM_os@%Ya(&p&p${;Zoy};`C>88f6;nbevrtg>;d%CF4$pt1GV}m_lBwlF(pUlY!Z!C{Vr@A>X=YGHhM#{O>WIvjb"
    "g=1Q`IVzNKq9<NX;R4S-rgE}_pRzZ-&$Fg)R3Tar4%)Y1@`C94dbZ)7mVySg_om2U!01!$?ylr@68N4m#27Hi+cf=nGgr~d7?cz~"
    ">uirI9rkkql@1y=Q96PgXVSI+jW>NOO!maIKA?dWtrBB&n#K-%MGDdSnBG!{<JcIZrm-X1mEw8#jS*@Z6WDf~TD$;2xS=sjP3I$L"
    "*Xw?tZ*yvtw{llD(P|_!Vitkp+C-ueOdRVRtMTw_{pB7ETC*?2khs?d1MgR}b0eH>JmGnVr#M;&VFs-pqrniT>DW?u;3U}=@o6#?"
    "B?p3|<L2Y<|Bn5@97|ww8CB}hw+bRl(81Jcpv-GSv;7|Prn%K2==U>ocwc4g!J48z)bNdPoy4_-AQpI{ZHg1$7*E7pVlls%@~J`f"
    "<BpA8w#i#EjPjBl&;#ChY#kd>hBRJx;aVh5kyGmpQ2NBjWc@af$j*e5`pi=y5c&R~60DF${Ed6{@4x>8J1w|++xKKKhCW@tdxso_"
    "dIR{SIP&Bc=_9BEXEBDv7WtlzG}E^Tag|y%^?9Zu!BY+o0iRl4-Zp^1$YyhwA8)Y24BJ<L%qN3BZ1FNYi9b;r0Y`(OWmu9u+WAC#"
    "igpCPK8`Y4mN%|-C6Qk5GMy_y7mCSC=u}^144<VFQH;ezi@Kq1e(Kr3AyL8BNVWjVl%-8weTC7HQr~eM*R=#ymUx4+Kt0GEOOR#C"
    "7(EZX#PBW$Z9SITuijmzjA2Xy*Aj%8vPY9-us*JMTq|RwSFR;QGv#ofG41;x9G|{#3CwJ;CDlP_GvN3y1kivp<)|g`3qdvD3))EF"
    "i%g=I0%*WiL(Y+eSrab<%tY^c7z0g(s*dwQ01e`Abeq$yIT#GS98mO*KYB91Ii2u@0GZ;LtgBln)J8xHUkIQbQ%GsyQ$3)?^~*rB"
    "Kf089e(cys2pr)H!86HOQMC=~9PtZ*G|3tJK}$mLQh<$_4_SU}wW~9Uy%&OQg1hV+4XQv@&3hsECYU?c`IhVz=Y=4f;7qkUWA~+K"
    "YJxj)mxhk}La<G6)|ELT2;TDk3y}n#aH3uH$qNBEdC=t6C9C9xARPRg38<=quiO`cZ-Te7Duo~B#aN;!p6uz5*dJlvksbeH%ut=Z"
    "GHYLy@<!b87Hpvtz7T*Twt5+TDd<K~yguZbgy9Q8HsWlT^E5ZyHUEVmn}~giUPjzvei_gv@GP;Vt`#_&2%JxK4JndhFpl<hnT?Bn"
    "ZTLEN?Y3od4u}y4MTG&Luh5ng*WL)l3pYeWs<%Z>&6i_K7-hmt-IdgNi~6V9!Y31!#9i3tII7U|JX`2A;Hn>L#M`U$T-T-#EAwpi"
    "yR^RL9n!9F+9x!O1KTZlNqq5)FHE-%V@ud%g*OaPF&V^divvr@WREprfp~F}SVAc~%uxqB+&1r-uxlD;$099pdc-Pu9u%9%mf9mL"
    "h`r~5vuV6R!zkFYB=BDbo?-jR9?k}-TT8R0qFL}l&<t7Az%24!2AK7nNi0ho_hn!i@uosq@G_tb852n6`R>a=GU7@Dvgm~X8M4N4"
    "4D}y7I0pT3*?OUB0_;BOUoG@@s2beo%;;ifSJz+yeqP?*I9DQp&5f;vET=NUv$EWkHMMIRCn90ZU0L&NO=U!GN#I*Tp-G(l;(C4%"
    "M)4|<=f!gdvzwM=No-R%7{?hc#*;Wh;1u#+2uC98(FttD7^VM0eBrjs*PE*P+EBs`rQbkIn0y&UmNsawHu<hgiyMBFf|V`LyD39r"
    "i^6!USBeKalpNI!miNNT$s1Yr3f>gx&j!4~M^%mQP$nw9k@-|t*Z+0>=J(InzpmeW_*-8f_1E_w{_{Og=Zog#kUJC<20vYY{QKKC"
    "*Wcl9kWRlE&z~55ASSw<DHQHQbLmdjBSFL(L+N?|aSjf6Udxf`98|gK>h7ud`FS8WCJl!P2GcO-B)`sgcnmZ%ZOSp-Fu2=c#DqE#"
    ")Y$>Ey~TJtp=2rLr8r>^OBkT0+0#t2DJ*P@Tqt$Q9QO{|7K=@w-)yqBEwBalO<u0|MSgoneq=p-=gz}I(``sGh4PlfGsO$~G28kh"
    "FWbecqNYu6DF{NLdrF&zZ1%Nm3=cJ&fhima^5jSthmIWPDLWUGjm>*hz&17A?JW?X=Ily+c``_vT7nsvkVo~KVPpysrp&MlvRI3("
    "8ib9&6gEs*Qu2y{J2=3hXNn{inNq6rla(>PSY%9;6Y@GbL)5X!7=^r7D{DA3nW_HWt%7)ISW<(bt5vWxF!eQn!UYzE-qHX?eM<1A"
    "fk=Dc`Ag%Il0vsKS8>d_A?pE?tk~u(3`eM@@rd;*4weR?eNm;YdcUpo33f|t(Oq|o={Aft)XqX@X&llLA3$4N7>Jl@j_SSWV=(Bb"
    "vVhDB>rRpVAXprU1Xnx>fLF3F3`l}03E9bNwKOWpqLarX!gjp4c2uUNiUD!BG%Cqn6e?H-$<okNVq++o)9E-%V-uRV8}Nc4GQ~?H"
    "6PermU<ZD*G$sk}D}$u8Xx44jV$AXd08@{WNxV2BsRacuz*rM0W2$|m7aA|IN#tJ=PJk#{8jOgaeVxh-R&Q}UBA&?o#&;?TlBH2e"
    "<w`kI!`3YebH*D%t^CEUIE|~`jpIV5NAMC@(@d!4;R<<@W~TcBfU4~pyyjL*gA`RJWNBUC%SsmacN0VVuI*~{DFxxu=tLX|qX2_d"
    "fgdl8Ons$`cnD%sVv0?BjEc($Df|2`+kO@O)Uk<w&#oNuWW~9Co3SP5TV;^+wko%Ik*B<y4|S)b-xeW77z>JBVKG7vO22E0H^(~5"
    "zU7!0A$fpaX#9;q%R5o;fGL1VAPowQnM%vv5fdpcP=;t@zzueYKi1Wjy`i2ZbIl0iw<69<<Km7`8q#LUab=%iP!Ua_Jw#^h;L9l5"
    ";v=>PP&ZSrv%0Qe6C!_Q3M-=+;Vo>@C7qZ;2U14K2TGzr<F3y4Z9j1WyXmItX%J%7`=iYRWfNIBRP2+gged~)_Ul7hV+KGHnj(lX"
    "I}B4eH>=1q#gU2JJOKPF`(i<3G2uknk}xz!4!SsS(j@nch*Gw$Xme84(mHSN4mf3^<_#TX|HQ=g%^_qu6Flz=+87(-$7C)Xz88TP"
    "n`4Oq7ko3I>z^@>OlL#v2K0dlmSjV0M;x0XN0aU|HGf7KNDir;yzCRyp7dc21)(jpQ?DCJqcQ{w;oafsemMFfhshYl<2v2pB%4|r"
    "ECX#;6UW$;p%hh0+`^9<Mule?-h-Ay1}{%FM0I9;gfvb}8_?#jB$07c%8;A)*Z+wf;Eg@LhJ1)-{#Xw0j7JL^Mgp~=gZ`JtJDQNf"
    "c)FKLdBe1D>Xrfhl24khT_y_OCHcVQ4q7gPK>`$y3avF$Fd>ml_Th(~kvERvKUfm4N6F`2f*WH{OplFwGc&>Glz}HaT5vB=vUQwE"
    "W@aHZ`-hs_=-vLOFQLfSn=UVQd`=Hq<Lh14d~K`a`nFDwcckxu!=OQr0@MVQCEY3wRK>e`PT|Ug7m@i}XS)=ehO^5_j$K1e`bu=3"
    "lgm(Ppu-q(0CAGMMmU1xq@3N9Y5g^m-U8^Sz#H)1r1fp%pjK-Oqb4xmA_I{ngsGSwP<Bw)f#(5B8oEbiK-(#M?2GjQ5;@(_62eq<"
    "-5pyzbUY0lC*d(dAX;go+)mJQzs`<XO5zCROMYwzVX}042SX_LDf?#Kl~C4^>0ib|578b1%nB>Gaxr1G6@=&bo*hiMnEasagB!$_"
    "kN{2{Ryj*Npmr3!T?xq^kByv^qmGX&vv2L1Pp5=Ff}Smo`l<lj0_xt~@R1`uMv4*v7$jL_fRNA<#|k_E7f|+zss3WUVyZ^@*!!r^"
    "Sg&g3hPq)}#c?|m9)VzKp74IbEDPTba+ol~Q8wS9IIWV%4vHAD)DJyc1)&{qG2ur%5(Ck$9kemwr(sIUk>+GYD|^rbE?1abcv5Sf"
    "moUr{yy(IaDukW-%h!X3Gp--I_VC97TS|up$4l&ik}+$%2fpV;zCBnn=Sp@!@PcGz52B126CDtO$hU`5=3FTq5IjE#>;aZBYoe1&"
    "IH=QHiYu*=i<@}Ci$JfFJ}$18tX>3w$!00kUd(+F&^73i;st};nML`M5Mj$Iv%-{$5TUnv5#Xh^Nb!Qek`X78J;1BVOaW?rm@&f%"
    "yCK$OLkL!w1xsBNpg)&q$&M=-t=?v#5CrdNFsNo`JB5B9rd{E*RMih2uR=T6G@Tvc2oLQ*(R4<Bd;RhA+t=^j{o~JXF-((0z8xT%"
    "&XVGscO89{^>lWsdV9zI%SSALn1Ex5spv3aPsEXgK8~t5nA*umoWZb!Xrgg48ms9FnUq3P)_5n=zM+Pgtd5UU`;TCYp+HBM`T0Qu"
    "K#zA{5KH?G7fMwkI}7Emdg$Lo^EE%NF;Qp77(h=noQ9R=Bc4uLB$(mHvUzOphwW3%ZUQI3N#=TFJDGtppVC)m4>;3Zcl*r2z?nf~"
    "k?s}tX5a!3s~8=QOsw>bm^qlhai@S}v<s1j3;rAoK&xSFID;S^*a3pE4D>A^62}e*jO0L>-IJ`s*}(u=xXX)kBP^1+==Y@nvA>!f"
    "6mYVc(lv!QOvYX{$?V{O-0;yq!2+-_81n{mE0;iqyR?!029j_YaL_xyN68}i&`*#{qD9bw7?3T+G5<Vvl0}e$kD~w%qGf<W8D*CE"
    "?jq=*6x}32un0o%Ia^S}y#zQk-Qkc@gk!Z@1R8i{EldcS8x(Hqs!^~pnO|oRUX#9;4?==7J0!@a0u<wh+_xl|9TsGbytW^SGe0_D"
    "RQ*sjTQEFRiU3Bl0|au>wOheL7|ae5yf=VyFhG}@8z{61lZofe4i)`)KiLf^3Fp@$1RJVO7bWw<h2R65BS2bx<jxKjgVBLl=&+g{"
    "DEcxbP-5l=2-%|;{H1-DPCg~s3RZ|Ien{rfL2p}f?Wa&`+0$H8NettDpD`nJ$1}vNAO$-6D8WehB<qab4;@tX-Df4wm(o-8G`GGB"
    "y|S!;#x!13zx5#1emX0nt+0r>@PZ^s8h=fTz?|uYjA75v5`;|P=%n{CZ$o1RPVmQr*-nGJHHt++Qsb3&VB)}%)-jF;^`OL|C9lQh"
    "EXWi~eKVkr%?g@K;Y8aKeQdxSTcW0Y4v)yn4yH_GM&B4q;Sb_PrtmW@qonL>Ks)Rgo$Zu<zpfhucCh0j!gm;jE2;H@o7hB&A5bos"
    "ZGw(fH(13sk>Lk)tAert?{k6HgO>i!P5+pcG>p~_gUBWd{D3fCoJiJBY|8u($u?Q5c&_nv$FoV6c+ujJyD%tkKEZAuD*A+4Fg5c_"
    "_yed0keu9iG>OAtMnUfx09B7pfK62ZfqmVykoo|1CZZGxyNF;uFpx}E7G+#imh?76-yZ|If!w79O}M1uRH%w|hXWeTggx8up)eS-"
    "v<8!$9@j;d0!uVhfN~3fsm&w%b*52L^M#!{4Us2%F$KvcGT7r$=E%9Ll}#1F9*5#U@QsAb9_J;o*-wiTEc(vMA_`e!v47Rh%Cm@4"
    "))*YTX=tEj56iHI8ixOTe@<IfZij<uy>R7NM7Oa_C{+QTlUM|~ag3;^+w%wYH*@ZYlF}Q7fkn6)$%cMXy~wkPN#l5-;hF9#PAp>1"
    "I2QEzib-e@TP|fp%i8xMcM0S;owe#lOMu78h2%k7s`;^Fn1mk>%75qFje)?1Nw@%bAS)!VUKj)mK*s4fROswy4>#oCJxKf?xP|1<"
    "y&I?ZO^NP4wt_MfxsWpvtpdlTd<f^595@<#7GYyF7jiQk&mwAE$c8eia(GBQFS4n5m@p)^$hC?i<2f3f9M4_ZR74E;5u;egB#2;Y"
    "Zjq;rGMf_q%H7R6OH_$bRth7U!yAemy$g53l(6F5_7YF;DBw?_yG@G%X;3RLYGgz0Vih+Gcffe@Al+jUNyt7`y-fS6-sFrDda<Ra"
    "kNX;-xP&`fdBaJ@#>&;hxWN=Qs{J}IcNx^yU@wVG7TxTNlskT~nFtO(kEXAhTb|ff1t_r<rH4&^iw#zYmpW^4!X$QBk>e#d;FiKL"
    "@=ae+;Q68Md3ERKW>{e01+E@vS%4>HMvfcnzS*%7X@{L-US~UArh^V+$1{Nm8%*J24?ITY`K!PLKWs6C)d6)@k`=Qu!53>R^#uc-"
    "7bGTFV~eF7rOSKFv{@R9PEyxBUvJvDg!pTz?4IYwOXJZXlbW4uZ)r@z)aLxQr0|vh63_@n7|^+P!<PU?edf2vG|iWQMVQdopp^M9"
    "0*QEPIurVMX(Y-^UVckQ$aj}UA(1eSU#Ht6TpEb_aeaK&0(o&`$6XqaPSfjM_RrF=giQ`H4vm3fx=_H{T^OFk`|B0B7oaT(rbOB`"
    "-n<KgRG<3nd5*g@Mqy?w7PTQa3j-AAMm|H@OzgcSOCyy?<Lf6cx>2||O7*$yuIoEXqZ5tm(JFyFH(4B<h%MbJ@SMdJC~f2t-N(_="
    "@I*W*wo2qJjZwrBV~C-{j!x9#NJTtRuM-ZR{^CX*Bm&}BQ6qPw#J7P?ylD)x7HhB<OX^^SdhL#LCYXW2v?@c~d3?**#<a*)WCNfK"
    "7*b>n#tU13B?D$oRi>3^1(OVT>aP<I+NYX;QQ@giUeh?hbzKz&O#SIw106cBvUz6IO?m{ExB8pEQ1Ag7&3*xQ_n_w4UzRQ9C&-->"
    "7(tT}cl_H>(bRJG@Xx@Q8+u0YWScSmIz4~2G6AVwSEn$cg-*2$^;4rgamX=vG|Xr7d?Odr1j2njj2|aP;4_*yTp2(&F$0*<bO+KA"
    "CxHp1j3&>sT>Ln8OfX|KT{-&Hj|a`w+-IY#C4L3&pwRi*=_<)yNs`PY(ny=1ZFl8_GYK@39Ug5ro=KwB)~Ws~nM<INY=5w!px;20"
    "v1AStrTN$dx*^0oNv7szb;}jsn_VWHsL{$O);)^q-67p#f|_d-J{m={c{~((iCtbJ-{`_tFY7wpveyZ7DrA!{eBVtAf5xPWMs4Kp"
    "MxN27Jf60gOo=)9iIJ5snmCr_W13g8G7;g?gkkO^IZ8bf89sLM@%>z7s7xxid0MO??KICC-v5&<$V~;E2YP-Ry0<{CgV4wYVTBn!"
    "hbweLBWb^PQElzY{Ese!@y$q<m@BK2mtySlM$9VehlH*-kH@rWpd*48C5egrKh#xu3n@x$`OUUEW{R#{?eaYiIP6WeHQNo264?z7"
    "j-exB<XkY}L1YZ~rn4LBtp~<Rs1Y|+xzCXaCp7YQnlPcY2zPv=JQWi@L_=`bH_Cf6VS#=ZC&}SvL`LXg&Pkirx7noN3OY@;IJrs5"
    "#gi^L<zpy3?U~pO^du!0E^zgxs@_a2oFu@7o*w6&ixDmB&C{&{cQzhQQr-0CiL-Gs$Zs1Aw+UwCLQQJ(BfSOEtb8b$Z2q7$C!CQF"
    "k-ioT(%0tPi$QcMOcsHTL84mXE`l4U*=hbF*rBDQdBI|+A@k7uI9d!e)a0{4YR8;x^ciQ~puy3ch#}L=2A;*ac4ClQ<}QO4=aaWE"
    "jO{GSj{F7GJX~)P^f;G5HpsG{ixt_VwXIsJLG>MH5hOXAzs4@!MewBeR*=OIErKVfS!z+T2&gEjX>PKN>7wSN#mg8i=kmNFcM$|p"
    "le-4)j@ekiL>KUd;kgK$)grKQHnA)Y7eS4)*<`-62zH#Nkgb+6VNUbL;za=DBw5T`d5gfvNrsp^X!<;}2oopAT4*AxU_m?~W)u_8"
    "3zBF-U?F}0ZYiDKSq4}hD)q}$R^u%LELiKUE7G1fXm~VjU)t)BZ#71C*(S$~$8DYM*q;O^O}4GdomOG*IPR5<RfV1nv&Wbp9<B<)"
    "aAg5$jM(wy=3xBT9BmA_;akH%*tLK*kleCHf9|7<9&!#h9X9tCjRDr}T~>-j2F%Xz&Cx}qSF*Ls86Yso@L1(+UnsiI`QU*>wndN("
    "mzET$*j6>u^>K5Qlm^cN*w|!E<UK@#WUX*%#zbaB99S@q2D!2sw$Q}>4%vgAVa*RVad{)GMc;`%bMTqUMX&Jrj*ZyPR6b5qn8B1c"
    "hMlSGDE3n5MdpYD=>q}+MW*W=W)DF7)je>;7)uM0=h`1u&e1RG38yP7umD7s*b*t#USPqHvc=S~uI@9)7s37VRu<GBTdWP5Ec;$!"
    "!TCFuxgVyWyO9O0=uG=GN(w{=4M*%VZ9;=#8O%)7mE3A4w&1K8%Yl?|&so_3o{{AH{B8^GnX$wN=MNfi26OWu^I>g7kuN8%CtX9@"
    "WHjH(Pcp9q$A;5oBm@23mN2Y#W~R;$z8Cw+oI-yl1Ke}`a8AKj)4&5awh+x3d~mkuo6N_8cAE9>J5A@jFt%XSnDL|bqKh00sBqGb"
    "ZOC8WsUKRjF@f+S3;q%@Su8K}Hm7EHGCPi&OiD(aFq?+?qi$dh3?`hMW|4Z1YavQ(#FNY=4F?_B+__oNI?+0bg&?sBH!`m@ip^2Q"
    "gppx(sqdSkia9@hj;ZT==C}eSxaa1ZG!jtO%@OgB_nf5U`9He6&Zz$-T!J_J(}wDZEhr+E_}bUift&7hQCmPFORR}$MP6jXXtBnW"
    "oEqghfd#F`5?{^N{J0jic7csN7O?bl8kCjpM2}hY?xfPCLnRyN_{C3<A8G@OZC|0>FbvI6>ey`xbaXde(e_$J_6?Akcb}~or|H8Q"
    "W_v=^3eHVv!EsV!V_7U1s%qih-c?8+S1Qtp`T{M_4SjRqxtOq0o#lB!U=A&pQkJDzt_^4FrBp>-mXlcUtjf_YyjTIh$WGCQsgvLh"
    "=i^ARH+@E9^46Qj^|tWA3v_ogNHd66=73<zk__@Qy(u<_0aJElLa6VBBb=*{f+nW(s4=FtIxv%NyWV%~3pmVjy-(REo9>bRK)OFP"
    "VXiZ%XJbGBgKC3I#qm60==%cxKZHX*GYAGoHhlINkVneG^UcTEvXcx$CYd@TfA7`L(3eVuhiC#nbp-`;G&2%j!!CUk-{p0CSm;t?"
    "A`DJ=+=F({5DLECc6A9)gg{sN=uk$81{~qSJh_A`8m#!^d7|d=SaFk6=N@rhi!^7}7aUa@L%DvaYQ`Imn$U1%GFA9_xy_55icW8t"
    "+Pb6g8gII@hUs{9KR6EY6h=`s*$r_^Im{ZSc#E*3SwMqm@dS=wb#>hxvExmDU^H(Ee_!ZLfhQc8AZ1sihA`=Z8xM13!$Gr-aDH~U"
    "MHYsUDb7siL~hCYpNv9DTNC-FKr@yh_`wxZ9oaSBoO@Y=**f7O9Etbk`lk3|#LU@O=ESDxBTQ%!{iRB8@=$UB2DP199HhI7S*2@H"
    "V|}x!s)BR}Q`9kH10R-BX`&mL!;JweyPQWG$)#Y9H72}tF!dc0Fl08m>zHGX0Y}V6FCG<c0&~DI;f5K=kCZOhkRfJ{HYV(#yPJHx"
    "gU(}f%Qa2@l`qPIl4Hb<(ykzg7DXCa1|$xR>-vl0jbMo9v5UjAB<z$a7AFZIcf3U*r^NZKlql^v@uGkuE4L&=uD>YcR5j@E+6<C-"
    "QQ#qlctNI{M2o@>z9n^cmPQ|9NNEP)y5XYe!;8##Tryr1b|~$iYost&I9QySeVz8Y;A&CmIW5v~<3*uIcAxVce^JO$@=O=To3^Uf"
    "hqP2wQg;wDnp?gpM#@c9#G7PMydi#+PBcq{4l$(r^<95a=pmNW^54)Dc@ACC=0(-=(yIH1^a!5OBgE2~&4RH=aFf#Q&3Nz7YY4R("
    "#(rXmcuFTaHXX)Rd|;k5Z3QC~tD>kLuo$6tyQ8lkibXuv6kJMZ;?6Ua#0&XurV<pt`KBPEHXnrdOs-XQ!8RhsRcGjG73blGT{Io^"
    "<om|-Zff3T3LFMph*Il#Wr`M4m_YwMKO>mG7MViCBra$~_<C38d-BX1!@?vsKqk^%zHKWeKcJ4>Q?C=x6cna#1Kl(8+me6++Y1}x"
    "!xT0|^u2#Mo?{9VQ}~c^lAe(`UIIP9#VvV&?(z#@hoXmh#vvm{aJ@~pciE2TgmvZD5`M@7dQ`HvTc^!>Q&(`I!h^88o+(%y(;9Qp"
    "<ek8;Z`uP)f$3fUyuqNJa2p#V#Uvhnd;RhA+t=^j{o~JXX%2W|91mg|LX(4ih<CNplJzd%?XnW$sZ6DS%pe4&bpvm#V;A@hb=u{Y"
    "oFPO^=VHfn8%l6B5B@NHtP?2@byxCq1YR8^H5|-qscKYvAQu1vsKyEfCR#}ncUAQj+W6+pU7w5oD02}g2+ZycEMvl<-8UpkSN-)7"
    "8HbsTd4l|nD|k3+8pd8&;zz!hnvu;w#ihKJR(VCd2{h$wVEPf${?j(QeS|del_`EmI#S6@+qTX(j30(#3^h|Y$%L>7Z{;mOW@w5T"
    "10@>{CcKa~lU>d$@LS$PJSpFSZXG42_`qvId4eK;@v;sT$D)g%XJ2W2gI(DBzkeZCBGcthVMRD{{XzEve=Y`;o+Q3!3>2bW5+p0F"
    "yC#E%-W_I;^0NWQvFVP-B1bf(V~^5{-A7G9p)0pp4cR{s6R(PUO1U8(Fo#R*hv>Ku5ZT~}QB(QHQWX45we~OvMj4-gi9k8}AkNjw"
    "_>QsoPv%3ZkpVU?MT5ND7y0d7dxn)ws@3|dN*(P#xna0k(cA=1fF%!+9@dTLp{{tToKS~@XdVvKNC6a(#TTY8T4X^@KX4sS@0K(Z"
    "4-{5coOJP^SCT&uACP-0B5s)8r28)C;$jd=cU--?Z>F=UxhUD`)Wah)F;P@)BO)}(%3A^@Apd<0b@>m>76mq<a0#eDHkcYIxbXZX"
    "(1Pg`a3&MqSpp}tSa0lxOJGFrCJzS_r{-lKqf@Y20vcqr3C_U+sL(rpl6Vo#LHLGbNHQ7B$(?B<pjJi0ra>}BFe4iZHu<g<bm%KU"
    "2ASjFerl9}7P}yzQX*~c7%Z$@8?XTSGb(2-cL&mvg*Ons4Mjt3Rtmp8BErfBB8;IeikOqYijpvbv{H};FFcr)xLPle#Gwrg7(t!q"
    "cPH`6hR|a@O13*EW+__KknO$Uoo`V`<?tiAR$GwxaXge%%<F|#w1APc*}K?-d5=!K$chdyjsq?1;Mfp&Vfn1{o$P~0Kj00-2nj(A"
    "4`Lf)PM#6i#kS6m=p&#>h0uNq`d-0MHm+ziP{@#YHUwPM&Pr%c3#L!@Zp$WXTPkYfp&s52!Gd(dZB2#5;0<Lax7cVJ5Ck0~$&;Rz"
    "qC-73V*wThISCV2FFeDC6biwlV+PW9u!gVq-cq^tzknkwFk~j6YeUM>V=dn^12)?59oCKvo9Y2iH`^kooSDFeq>raB-RsyvIb7M0"
    "_VHY(PEM*Au_E>iRob+s;?KP76lYyuhan^dI<7*wEAMzlvqV!0t+<|*yWfBvIaPaQMG-LJM43eFhOrIkn9dE4`-t}xIDriuna&UV"
    "kr?sqz{zArq{{3?c9e)Iyif-<Rg_LfP5E<k!zGD{^kNWmI?@}`D;5G6YBHzGk?${tGRTe)Bc>KDg)hWMFGX?e+JF{}AnxnxUs(x_"
    "2iWz<6G_YT*xZ5U&6vHzVY--_1A(K#sJe;rEAn)Y*_PVhRn;Eu<~rLm4SXj;=sfiXG&ShEkl26@jGvWE>%|zFY6CD85qEyQp^gi^"
    "j;h7*TsI1ApvELd-e3PGhAKQ8a50GkC5-3>Npdk#fFcg<K2*Qc_UjIl6>ubCLxvs%bkSe|W{dPl<~VeLP9fmQv}q84l|Dl%9!Ypd"
    "Kh2;e5vYNprG(ZDYzG+)kY_`kVw7o~k*;a1W}uEqtID|9XwYsjo{I-{)GTy{iZ6^KTVp~UXF6!*H4A+uh#L<w8iE<<?=onb-%u@k"
    "sCyvipid>*V)&n{my*tL3bpDEZ#6p(U<$q5fI$o5IiTQVK4&~T67Zlk-voCR&kh5FnV7LRI}qp*^JwMF4h14&L$ASgon&@2z<z@;"
    "C8O6~MX$`cae@7X9SVVsjD`830n`*Z>7MX0oE;v3iRzfuJiA`9;`y_~gwBf64%iK&*%1SWkrHf*nIoh_tY(LfzNuccN@j-*;hC0i"
    "=A4sr7Dlink$W{)?(B%6RXaz?>~NutV4Yu&5Poi<$~f-q0MQ56*bex!LxS2A#S7+FA^03kF{UvJ!r8$<Hq>-oKbjo@L}rapB;D2Q"
    "ARv=_<lt^*)Xc9s^pzXJ-{{Bw?2zz~m%HkL@YqCM;PGrKkK(;}IeFRh84?_`Oz(7T<WCqwRO@o=m}measEQ89ZsY|9f<A&`(^8Qf"
    "GYxzL;T}P;Yqw&0=yXYjF<ICP5vv=-pe$-t4y_PHiKw(WE}xg>c?P0AnJ;CS*kBx65SdsA78x3hVhfGL-yU1aPw@SPMhGDX?rb}X"
    "#8k493hcJCGtL|ny<ikYB?gxyCZ5F+R7I2jIC4z@;&OtzKnBq4-2f;qrz?hrMNVXb0GAUSoLorOH4yYm=;{N?Tt74s;SmItd3-CT"
    "Iz6*|s-wXCFmaz-I?s*&f_jW+7Eg8L7t>Hy&n=xuZ*XQ;zKKyzLjYjf#~s4^aOlS#ix>I4kF=pYZyb{B8%XGFms5Ev4_nq5MzMJV"
    "OvrQZV7!k|W`IfHh$pyCm(aTmmL*58Y<SjseONb-<#xTxn<G3JSI&FH_dWbQ&kqcQbvWjT1{}!@Lq27hCI^WrVUCfcj-YsM8dzcy"
    "w;MydpZevk3?z00NqxFkY~qq1K~+rgavYQEbOgcSw5}*LkXigmy4#^QP(l(VDl@y{blUutgH_D_%0OVp6LpEGiDo>WsE_xJ>lg^@"
    "XrjNk43EMnUL~~AVPta4nvloA9shrK@7mqAk*tmW6|a1K!yMc%IctBAZ6>2VisX@{%w(7>E|3Hz#2~-`q-D)s`@f&AOE=J<Y?DM2"
    "^n0}CVu_ckZgf@G^{K$Id&q7d@-CXjaUauI{)KP9oxze+a)l#iuozva$UM~m<AR{iE!?61nTeW0@`0oF!P6jz9KW}GAHzd?u4fk^"
    "H5!D4)?v_xo_bDSHPDZn_SSfw-9tUJ(#Tl&l~ff-3cRuE_TsUr6m`mY4<q=wA$6)rqex~swZwB3B+RZ8_s>>Xu8Zdh4f3-CyHD<{"
    "FB7t5r-glL_c7A@@*{5Qg3T*{#5!JK^ksy8<N1QJ7{EIlJ2=X)J!jF5?H+ohFFUG=k<#FOT=d?opb7UVJT$V&`<NIA{3KkOI$5T2"
    "uwp5k&YH(5V(bs+DWZoL^|2=y2uVWxP&;rXRtpojO4+WAl^By&C5F>OSsX$Ve0IhU#2Q(@kM(dc;Z3qy6Le;1_t64}5N6ONGF0sz"
    "vVI6rw%a&!7{>>M(XogYdjyx_daGLl0STUVo@a3Hu98nvL?l71H*6gf$9PtLqMklL{+)_XWh+<*CUM2%*5vs(E+BPB<@oCgvDRwY"
    "M3~4HH4UW%&UYB;(i@zVa-Cz6FtYSM!IKfyfDd4~ex&0I`X{B#%k^CSem|aQo}p@_bH1%pAp4L3>}>;%t*~%g1;<gOd-u_4Igo!V"
    "aw4v~0H-GoGB!`v3K7WG6XNk;3@r09LHv<UOW$x?kf(HsGjcY#q-MnZP)1sSS&?H({10UUqNYm{%D{3BX@WzU;78#(){t63I&1W1"
    "I0~UPs7_$|%|^gUu!BKWgX95BOFW4Fpo$^;gsBvzmcuw4R4tH2Ju6{Q6i;<PF1$f?0&>BsIqE=*4nkXM<-iGtQ~^Z-wim72>&!bD"
    "f^;QO6C-~}!B?}%l+<<Rn4FqERFEg@HN`lhPTx&@a^i$_jB*waDeba8u?aMVkvpWIzc9jP;G3T~*kYY--~@(*U8+vmbu62XnI*_v"
    "w=iSf4ube;={Bw4t!pNTO$q!yEKrLVS{ROJNYMA8E0p?zWE>Qn85uu-F4nC*U<4&}OnLMB(3eI{wVfF?@b{q&_X-g8d_!Wr7tt2<"
    "u5S$})cQ^02L6D8U9RyR2xuA^kY<Tt$5X)}z^i)rL31(Akx<#LiQ5`O$1BEbm~FFsK@-_=UdHn}sq5(?tv|pNjddwB*s~xUnDE*c"
    "=`w+s@=W|fQ!W{(ug-F@a2T19GF~6Vr5po-i%?<DvQCJD=2IY&wl1uiM}y3^i-&a66qaMugyCi=sKky+f2lUX7$41svra;9K+Yh<"
    "*4?O}Q?26<%uaWEJjX=nZ<)}PE$KTymT-_E*7(tB`ZSUX%jR-8$FTtwA*n*O*-GjtDt}mH%VmoCgL!EnSj00h;?&EMBJnJQKFH(@"
    "K&Hq0jJYc)oSIx+{&IPTl43w<97y|`(5?d$F}?#yL-8ftgP{TSa3E<|v28&14<t)6fG)HFNmm2g^iCCcCw8mMxVF^wJp*#gEmQK_"
    "Yp&wpzFN3z0|RmkWwIY_zH14iQek*DpivLh%k>~s2oR19h%EdGZ5FvYNV6p$Qo^mI@?A=l<*87a6=~E8zwx{8<4zUraiY6UvP!(d"
    "no_+%7&UpGtN|j5_B>fgoK}7-ChRyg8(Ag{_#;@s@RSPGM+StNtSRw8R;3U#-T0B^#LI*lZ9s)N1|(Y8S=cP!8U<ISf2MQr*UJKH"
    "mKcy|3NJ&3t`>}C*kYJx%M5N2sFUh1-y{T&t7ML_R0u_`Zp;ZA*!5%L_yH#BYy(pMWtvLkD5{ZV!l3^$RrrNY7rShc6B>~CFB5J2"
    "8nssFe=s2AUnI-j@vRMZBLm_LRVXs3GOgEXp3}izQ9EN&!%RqO9FPFuxA(9AAP=g)ARj@VA_7rpBIbs-A<w?8(bObb8zJW<)cMU*"
    "^iZdp^wVadl1zc+8Ia<`h^u2q-BSi6n!ICx7u<r~tNV|Os$8!@V?`a%?c<Ia*7o8}3Wf7&mQ!p=XvdP;yh)=2ijZJN7)%sd4UGiV"
    "_a%<+>ty+D6M^-889<W}&R1l@=oVg7GrMyk-tYiDKXSOmI;q5EJ%P;4^dn6C#H?8qI0mG8FUpG&gnUY!Yq1?05a+$<5@6W}OxgsY"
    "iCo!UWYJb>@CS?)(CkH=AMli1y6Es&o4A|)q)BN)Z3u_B6$7KO!3c)fw8p?6d3>&5qybJ{q;sr1t?~ZS3t{6vY{(uRvHJDn6a|T4"
    "mbM^(=r1e*0H2WcBY?GU3AS(bA)Z4bP@>f0Yc*^&0(U-3X2drjbKnz1dZBBtm3LohO%#AyU*wHONl#|a8%$wDQK;9)g#$4_At>OW"
    "J0@o+4%Qjoph3`?loJd^e>)SZUL4my)!;$T>2%Z##fvb$(2$GuGP<EoBSSyqt>b&<RKejahzoj^2?rJ*N9_8JIn{6oGmH#@4n=IA"
    "MMJ<W?^S~Kb#NXPv3abh1V4%+?<^_;<2SM|9Cd|bnbQ^M@sulZF$?Pt(o*%NnZ~k7u^k$(C^r(ui7~ZMrRx;$phb#9sd36riQ}*#"
    "EGcf}?MiHjydFI-*jpzk-=A;5PDL>cUlw1^!IB2eTzqB?fg)_9#n!$+QvzR<yD!j0)NJ+T<vL4incsxGV2L-gY!smy9yC-S9IDB^"
    "-Gjb*U51D-kB*%wX;qal{Z{Tv{k#(X07$(CVcl!_Unk#`RQw6Ykj!B{9T%OH0U01y`yj01l1m-*y!&bSSS7hQj?hs78v)m`ChOs8"
    "iL0=gIs|$|{>0?D$U;5?+<JGgGf0I@E#j^*`QMkgnz<lNWABtIfKEK!v6ESz6d&3JP2MTR{{_+*YH&{}@;cG76t@e;K#;CAFx@7|"
    ";ha*!HQJ4q)d~ikQ_8r;01X#!h-38dlp?NCSNf$n<R=yJ7l}8|-5pG}Rdp~O_TeNOC+G=lw%!aX+eo&UmNR&DQ0Xqjv95$eoz&RN"
    "n~VP+RKg|w))600x+QVe<U@(IP$o2pcr&O#%hK<vgjq~kN|0xR$#g^hsYN{)c=2s*Hk5Q@Zu{idHdGt~!ezFCc^<QRlWhp*Bbg=b"
    "0@c%r_GdY0nW!fe?$45MVFG&wgnV6<i|w3?Vw(~;DA*8Oki{_DK2uGc&MOo!7?AhRa3D8gTM$*k+ft)qRY3`5a5Rf5Fk!d%Wa|ZM"
    "Cv*%b2t#a@n@6hSw+tu_Lp*Jg>H*8%g4lp!>B$r^gE`vPRIg4get1UCw)KK41pBKXNm0hhB!0E3#elj5Km)+qS1;aM?G(voTLEYU"
    "%q?zb2@9yU1~{iGTW(}U;v!uNcU-vNe{D-djbPjXwRC9c)fA3>IPElfU<`u}Ir>=L%AbmdBen_)we%c-a|Bsbq<?MGtswZA)m(L~"
    "=3Cd@Y0K2K@ZI;etHu70S+cyq)q>m846&+N#$umoBJ{j0D{(>+!Q=!$=6t<*HXfJ-S$nKk;SbHlyW7iOFYd<E>zljj#q=NdH+N$!"
    "zR?syG%!=MWP!kodjH)KT*yDeRgVQKf)oI<YLRgzh?pv&0i*!^IC_2m8}><PioqU|3wvaZ-jA=Yu-34zj*x%Ga$d~e<HiGys1j3B"
    "8f8VL+>E*zM1A$d5u|Mdg474y^;vbJmFNsgbY3iD$e*Xeav*qQrK0SpuYPD~uk&P{uBcxtL81yBSgxOtkkE?BTZ+;N`|1ZMTo;$K"
    "xRK#Cx(8a=#M5RD377qnLPwY|eE2<8fieX!1yw(0Yc3+LWKlEqkpaq+c>EwC0Q=up_OW!G4izE(2x4RqY|56j!JP>J(g;c|uHxR("
    "E-rXAepe<csWxW~s9B4&X0GtI=I6kY=0|;n{8;Mtu7lwWMcu}=hjCv~KbATg1Abp&uhNGqC6m8%>eO8)MO_NFANQ4QsF=<gYmX~!"
    "ak4Uk7tov*?0ZWK6zf`|8hd@^`WV{usGYd4aCd2|;yczTPigWY6-F-J<0IFC73)R5#~iSOs0KvR8;B&B3cOI??<>a#QH6!-vty20"
    "DA)ou;q)KqJ_T-H*~N>W-57OMb}fcA7n4)n<F=@qE^N4`gTAs{KNrFX*<^Fs-J>pP-gRLih&xSn9|=t&e7SUz2!HNjTW;&o{q>vi"
    "?cK%Y^@tUE@4MS1PbB+(*jM(C<*DAzXeP$!{r&4-F7NKfZ+V~M-kQNAPqa_A<@VJb+ag&Ci)w+Gu;6^Cdaz{*tSO6|QU1~J226;)"
    ">H~x6SS^k24sXGxqUx+f2rsgO?_gE4LMaM^z12wDpX7u=8`e`uuJ}gGOGEPqKzqs>nE7LpX+Ed~cK(nR7<_WI{(vurf{b!eE>f7J"
    "=IPD31JXxbhO@x*3`9H-2M>8U6Q_x0<u+Ylj;#j*hGHi#A26K9U)?kt+8X}e7bV0bKke^on9;}E4aN*y#F<d=)0er5^wtA+Xs%>8"
    "h}Iz*oME2;gQ0?E6$GyHJnt;JrH0N**^=X&MZJJ+x5e5V`#jo39trHCg&BWlbP5yz%fHGghjLOt@k~W`ZB#MeHKaWDBg|;%ZYQ*G"
    "VzTfiNb(|#nq^28>_u4uhVy0acESoLUbno%;VGMu^S#JY9tLbo>@{(XSrGeCmpC>>`U%N*T8HC-$V=r9)^qF)Dc-8YmPaeNfi<L%"
    "EAp+O7r?@R0x$Mnocy$|%Zi(~2b6hq-&^Ku6b>o#Sj>_F00!$DGlCCE^b{dD<_RIP%&MdiZriddR}5g)+I0qzqPq^d%1zz!zz0mW"
    "fI4!aUby>IruAv~gy2%Be8BRANsHX_*GVGAC+9#_BE$XO8J~H2>Qu2KtLFS5Fr)$Yr4OJQ7+*1U(eF!G)dca~7r3Dz;op}7=%&No"
    "o=z>2GXQ1mc*g`99eZDRKH;E(2NBV#rdon{V4>es+X8F<H%W%**uFQg&{K~~tMMT;C;01U96_>O%T7sEL-Yh=Y+;6=@IJ(!(b<5u"
    "+Sr)T3)&nvOA5fzzzp(qm5F`W$h@TjR*|7;{_+V08R1JXlIR=L2nM)Xi|eZ-9j#CB>7^(PFsAx~?M^o1Y#YX1r0O|3#x#owwwecN"
    ">qSg`IcpLv!qoXsTF8$AQ&1ZL>Rx=3l>x%w*>Px0@2sDdnqeSx7~KhNbyT|uY*4jg%dHPRlUiqA`f5m{Dl(01lNxA$KEU>=(g(o~"
    "Vqe6bNtLrN8-S&Sg5sIfEBjLakUmqFN@P@h>`T5#F~8KDgutY>*p~~CPinNb#YJpJDL{EIdKb$oO`)h1f5CxW37`Y<2PSXFSL3_!"
    "tDkOfet9*|9f?)hj7c<_+BlZ0*0<6SMnj6DA3y1$WkY$kZ%B(A%M?DdM4`vnu_1M`FF*J1-d+fPfE6e(fAxNR_exrT6k_?WI0nvQ"
    "Q>x|&&bm(yxoo1`#9?Q4Nsqmg)9J`gn$|_Kt{+R9VNGg*jpcP#bfOWSV=C>z7Qshn$}>`m`Uzpgsg#pSiE~%*uuk)=NFfs98PY=!"
    "X+f=s#RCk1RMlmKpIyrc6#V}(&*mGkZex*9s57bZG=ax0Yx)7=r9E6+J^JlxCjJR>W6Bress_y1GmuK#0FzD{wo_5zH(Ue?*z?Vm"
    "kI65`kXq@@4rT-cQ+|;+@C97G*?PJVE-T)Xk7b@VG9q!0pzFX?W>lSZDqCL2$Ry^xz)fvc943q*i?}}!qVWtV1!RJoJL<o3DEkxt"
    "F1Y%VtBnb5NP_YwfXeMfh7>}T!We92pUlgu+O9Xii&RuF1-X2Z{XsPws~f0_!C<j(NIe|RNe(5-n<{&t=`&8JUMn7u6}aUvZ(_<E"
    "Ih=(Gr#nLM-ZgYI84gP7<*ZOxP?#J>r7e|aE0hkvjh-osLx|$GDxl_kU1ep}FauRkP@3Od*dV4@Nd;Sza6<reewfo3WV<wlV2CA;"
    "xeU+`8^Hc1X!l97hHC|%8w>MnTP#pyJjXVsJq~7w$_S=d1&XGjV@#18%uB~g^-M)yQK7fr!c(%jR!*$`lb|Y2N%oKx&{#wwB<rVS"
    "64G}Jsgq;*X(ePG*HjJHvCODK9Ii`%P_-FFb38}&T6|GD<+z3C2Zof+@f@lAv9II3_s}~0CbCZC<O~cda7GoAhN(2|DUg7J<qc^W"
    "wo9NrB>N<{g4iI8(o_XICqE^5osJ}rt5pU{#<xvXa~#V`CXNY~b_BMm(vD+UVN1dDOc^VOGos?+{Cr$1G^JdQ<wZRp-!~OIIfNIO"
    "CbV;EjRj$k<V5XB=$cCO9LWiv*}%2Vpi+>J?vV}r=nN{Q%}S$Ej0I{L!-0VwUu=Ebl-v0uL5R~FGVFjiqeuW&H*M;5RNAU-+Ednc"
    "xdfY5yacuY=rU9KLmKmVz0pHJ51H;;z?y&-xdua?Joa1xw1R8wga?$4ApE@8OX$#QMY>_<VOZlvoACtM*CeZ^AJTMPQ}hI!G<Y1a"
    "Ey-pBg@iRA0D1e0>ro6yuW%`>k75p(_OftDrqnpIK;_$`sOQf}$(>%8=KbHlvPT*&p%=ak`ovT+h!S9<o4db_Z`(T_?%UEc3}<S`"
    "taI_ji7D{Y<T;3Y{<Qbo-dCCb->@$Aw45`qO8Q14zrEM)xukBFE45Q+)7|niAy@kpNBeBb`$S7SzxEajqne+(DY+AdRJnb4966Q~"
    "jI4O%#CNv!qviZ)SwC3v=ilFiitoYx`R@UQC}FFMhIfygZ$gp3$QbXJeLhW$@BpLnzb@X~P2XI+|J(H9_LuS7Z$ce^hP8urGUP3n"
    "WuBG7Nth$@BbNW~aHWl%c*)ViNcF^Xpb|(^oBnXrFM9qU@4=ob-!ShypIIBv{6$yHF#V+84{n`x9@ang{J}9H_MS?4U*6|sLBNk3"
    "9arfdIrEm1)La!?3P+xYal%GALop-libeVK1zv1DHef;^v{&ox93mwc51<|+g{1_jjhY@aL0q-2$u5%C+p%_lhu&rXNxMMUCKMTD"
    "UR3drs;0#WLAV86Xp*e6>9*k3Qn3%7ajmpUiiGTWswL8og+Ak)zyYxg8VZkn<0`6^*Hm{N9fqt}Z#UX#cWAc;u)O`14a-}#onZ*7"
    "rCw-Va>BHlEiLEE=g)w;vt7e7;4uWYt|pG7I?sKaIXT-dAb9yyRv+qiS(Tt#E>XiRs^lrjAvLDkK*!Nl2r()e2+)9-pA*a60vw*;"
    "r+H+}Om3L2s3#fxqd0(M;Ku~#i9Z2VIh$CuI?_O5tn`P{DL8VJvFutj$I)M6?y(uK{s37(E~yg_dMf)xn$LsxB@C}T<7ry9-NCR8"
    "j$%ZDm6lcefK8hz*ig+#K*3aziQ~mm714w7D?47CKP)RN<Yb<#cl<GTP)5K;s8eyfiMOToj(;^UGptJBF!R6=+knbAmYrr)4%gD|"
    "xKroihX#&229(ONyezjHXg2!8jA}WS3z$zpGZ@aPSC|VZ`o$A+KD8pPvZm3=KBI1VVfui5M(wi7K0$S1o0;?J7OL;n6y5n0OOX^)"
    "EVD&E&ZkraAEmsV?K7$s<f8%`{>++14ejEa@yu3>VyAr{o=?9hcI0(vospHACfTOzGb@&&ED+C8+EU+!>C)wjp4d5cO}WOG&#Y;1"
    "0*-cFpHJC9bw7d4&(Ceg2v-}YptSGm^XZvduu-D7+gy_8Q#G5igz(d|+#Pb;&Z%-X<@#H&Vzvbg3sdI9`BV!rvJ`H?=FIAaILTA;"
    "Pe22o*`R4pEablPGpd>Pn=@nXl8g=W8)c1Q#%264yySTeVC;~u@?8U}WM7Kz4;D63h6a?yzBE0A5Ooabg?%Z?Nli{<KnLtgROTNo"
    "6X6i(GvjbN_AZM9Jl|{q<6gWK5Uem4z+rv1sMb|kE)hM*G9cf=|5=xLHh*qqI#vj!B%}K>*Fs+fa|JyEk_>*A_@h`knu}nL2+`hh"
    "Mdl1K{LytbB&%PfhcKOn9E&d!lCAnM*~rjc{zZBvf!Nba^*qUwsy1|a%fc(!MWp7~0p{8)&2xpRzs{2ayTLaV%^_51tiB1s?ZXKE"
    "rNP(mLjyA1iyx5f7DIuFD;!E>aEO%BEHuc2)7X#MVv!aw?@eV=n-lci_mLAA5O?N5p8qOU6n2X4;lm|o8&xt6Fx)}|YJmBm2%&sZ"
    "H26%s7rBsAC|i3FJFx-H&{!$r^ouv;-1`rPN|ih*@Se%-{cA(YVQ-SsO?M2D{FqW3gSx}8t(u0*X}Lo2gqPTG0#}P2#s(CEe1*qY"
    "B#>sBE)xQJm_9v<!;T4Dxi!#)Q^Oco&w%uk1TTc?`%ond>=-9ZM5zd{`<{w~U+z3e`d1ir+PZ=PR#H#PB@}jsBg+>5xwE0F^GC~W"
    "6?W2at6<nIhL4Uwlv5lvWtq?+htPkq)nWSg@o&V9H7qx(v$>gx%cpMH^9QUa--O0GWI23MjvRzFGoZY%x`$KtQZY3*I$7#8Ijzoe"
    "szD)a#<ZR|I1wc<puqZY;UX6%d=pYiaBLAsJ3-J1s^rFzW9Vu_(V=jPlIo$h3`I2$rE6P;f(r)|whj3v2NO2rsvJt!F=Uk-O4l`H"
    "njA{kGZbDtl&)_mn0P4N;7k%6o=JeCGs$mkD6t^tU!^%C0r5?#JgVjzU8MDgO}RD|6KqBkOOH<0xiOds*6+gLA1qho&zq5AzVuuE"
    "o3I(=SlNzq0gpmhV5@bx0iO;=ms<t|8pA0~-6we(nHsr8WBo8RgYQ&)mNk`$y1@a`tx7zxC#E%Ii8|#WA2Z#q7jXOINy7*^4fK0}"
    "Kpqqyiqp4foIS{#OjRPXGZi2lm>J=iR#4Wxn5Li7`Ia%8Y*&vu1OszLgL`oBB4G#Hu0B7+8<-)PPf^plY)^;J9GW3U7A1fpK$h06"
    "fA=VMph&e1<iH`S^>!vW6y&jrReA0ZvD4taPerwro{n_`KR6A+_QVw1(gy~eh~%<TfN8d6LM42aVqR`)XfMGw59o`E4T$_#sqR)z"
    "Mxg=8{wiH@o8Xwg*o4@AmD*q1gu)J-B@dh-q*GBXl7gme4<MYIaszm)jfh765F)z8YazCWkWJFHt>PiaGa#T%m3c?EO9tj;o+oQ@"
    "HBZt{WY{5X6JmM*ZNSD$sph0J0NCqE8n1V{?|`HVrYutRo&{?B{sJpE5Ho)}Yi@NKffw<Z0i^jeO^E3)w&|xeCxHIiW^{GZdeL22"
    "&=h$<IW!=xDrqcRxR=m11l`s&a(XGe2JxC$H*|J#cdCNJlCaDWjSo#+w2B?>e0k=yQj?SuzKif+7P2GdN)R_C)&~<O!u<tXe4EgS"
    "+hU$p05?5_hB<6oV6QL*&lHPjgk)72jsE5)zvk@up#eDufHy$U=aeHiN<vbmZcaTbI<x@#%Xf3ZqiIVvyaL!nV*}H(h;51BzEEud"
    "<8!#6%rznKM{}{r7}u99qW<E4G$-Vb;js)%=!T;iK^>A6GNuC0$c_zYhoc$d5AsdyosSW|8&m0__SYRpvjU+nq0EsH--PBkmXTdg"
    "*$vLAR(6cH^XeC3r)AJZ;W?EI&n?0lm=!^X2hll|4KdU*IvvY^sv-JBjXJ5BF@k&~1E88*kN?SY?!2K$zPq@+yS%u%`t7I7t1Fxt"
    "7a33^>J@R&(sg_01Yh=GedKBKVM0bYox}cGV}H7;fgeam1{8#}c*Tnu%*b1$YjKlf3=Jb4I3D<;cyS39mokjd5`V-L0VoJKaFGH1"
    "Ah~JgJSb$7CigD~lL+a9GMRD-kP`{Y4Or|gpA_oA45*2Qs}0H_J3?%GN;?&OB;2l<*pkI`lU6H)BoO<VW^33Z%Qc`QBsbCzf%!mL"
    "wNk^{8VeQn2f$(lC%}h{K!T{6&<?C^UUm+tI*s1}20I()o&mCTnQwW91cGdEmP%F}i`}R<tU?IY#CA<+2lYV3QJkl_91yflDu9$x"
    "!(7=H!6%@S1LBlasNBn9^5)|D&G_nS{B~sF$Tru21~JbYmI-(!RE#O!ZpZ&Uesjm$W(VQk`i4@C;5%wKZM!&1Tt`}E^#_T7jTcUh"
    "8M=>nh~2=6b){m2G!26zBC^m{fwd4M9gMxOTs=BzkT$)_sPB*(te=ayO0N)}v{E`)kO}bgwx#<tp&5p7x(`+wQ^`=3LJe%KV68nX"
    "a`kc{6NHypAud}`<`Dbg`ET@@q?t&CCjutYd!39!WPk-8y0Q>@3kJ@C1DS6ZG>dLQKw1QJgIGF(4d047bIMKZ#KSfN4f<TGmRJct"
    "F5_;l>*;Y1-vC+JDK$^lfZxH3gp@Qrcr1*>Qqivb4?<m<KQw96iH)m)uX3R5PTTjQz_=>-D*YV*DV&$#c)oEZum>y67*;QiBjeiO"
    "s~q4x(FzV?E1Bgw#+AZXIcdTet^>oyOsfO5s~c?Gr>p>6LFJg@O~KZe_DwK%Ck_JRdIH4&ZfbdB3Tr{!mszo?d60^<LP%{T+XW3V"
    "2|UL&t~U<nMqHv0TE}W?SD)%>M2-*TNCM0HVuOq;l|z|<YajElffE>4D+e>va=3jzbS>j*=1`76>tGFuFoBF~nlCYf<6b@^SAOi8"
    "H$o0(rV%UOu_9|wwetc)ELFZ6S@EDs=LKF^sD$?t*n_H@mw0K(l;b;IG^omXfgvSQfgKvxIPwL&B;_wSGW5IeBipfU;~Is)Rh2m4"
    "1Z@Sb%-1x!6Jq;AKR&{VfwjEsys8FLbXj3gX~V}uvIf&V2=q+Lu@rGbxn1rdt06a!bB827IPF%fJ1N0Yw7?sMU<3j<fFu>z(T)KE"
    "@=udRxfW*&YGTAQ+2&-5!3bV!Sn755P$Xa^f^0c%$4Bu`%K*(nmQMq`lt_q_Cfz6IKqgRF-8rFS>!Hr*wDkF#aBkY10MQ~l)C;GN"
    "V1WjHsiADF)Kq9B2S*(wVL}m%_U$}E8v>4F?Av;WWjrh+j6va9ffe;t0LSvds-d+hP+Q+qL-b`vZH()Bef7n0ypYancEu6ba9_pI"
    "ixb`wJN7)Uum0GZ5vjO-Xpg>ZHYIdIx34bg$qR3bxI;sCFrCsQT+}mysYT7k2=g`^OtE~98@V-6=-Y#77y6nXXn^-;j!h7E61Bm3"
    "7?K+l`jIu5meKjqW`(L9aw6fZ4W@0r#F5lGkr#)9X&wAn<1jV{$MbA|Fn!bILY@{cw(P+)O^XRIvRX3a+0kGcrptxWERG)!r&(G|"
    "K=P|f+q>a7!)cUVM&yRXzUL38Ng6(syVp^*P81HQNIuVy(j$U{bLbJx2X7fLUTB>|q3G-=aq>N<zh>ciKTVJUM$lxn%uPV?M;tnC"
    "U*+-n3=AfzOS#C2cvpgB2O>AL`f7{4skZR9L(lH59QLQXeWnXMbo(lTy=h{^hewd3kom6FSLhETUKT0zij`Pr8Th>e9D7q=rl~Aa"
    "fo%^wtG7JwO<SvEcih+u`wH*gq-9WtlT<+%_=Cwa^#7n(0Z8aj;@qTj9XE{pzT#Yz#ik;RT|B=M?m&&S=PdH;+nc|SZ>K+v$D@(;"
    "gJlx}78HWHt`$0cC465tZtm~i-QSHy4j_s+o)`Ij#Tx`xRn*k$1jf7aGw2r7OtjRE?FLqFPvCIstq7`XN3Pdb$PeLwU%(QVFpByL"
    "Ii)JJJ}OC?8@n)a3hnC9UYDS-+2RuGE8+VSZdLWTb{Khm#e9FtoOg0;&*>e6+@ET_DTVX9AmFd4_r)#ipSe;JvocUV1}AG)ZV1c}"
    "i}vg&4tj?bu_lj50R>Q=vhs9W!2i-laFL$vLV1PLmt;Lp2$qFRTx>>|w&v4=hV&h$ubgh4N<>!0;#UbqS(P}>ZdvAe`P4FWL70<="
    "6k1?~dwiUnwr3T$;5BB1Io`Y|+Y#^x$>L9~&MC0^(In@fLshl`w1Lq1)|ezv@(v=aiEBJC7XZ%rlvE2fTytk$UN|&I6w&&S6}#ie"
    "hvr2VRP*fbohjtO!2HluI4)n?foB_<8wOa~nUpv587AOH=(0*S@&tQl<VQ{r`QzLwhMMXgsj|~Csb?P3{3Z3uolVU^c%|(ppBtGM"
    "4$cVcUE8E)If4zPQf!kd<#0yim=NcjOyp%_i0!~X4t`eKip@JGBfFZ-HmOk16m2n-fQ5$sJXa+tvr)RZwY&-s(<hrvPI%-nBu3m|"
    "EmI9DD}?e&J|<a?5PnTXo7$i}O)BuDE!{GSj-&}?Mk!d_MKdTs#)cq>f%GOx4tD=DaY8v-(i^d*8zrY@L!Sap5j(wgJRE|4E8qc4"
    "M4fK&)AE4CF~rn@fbc!7)bT5_V2%Ys`=m$V2XR|LoS26OlzNe%&i3_j-+_Hn@)C0tLQDJ%A{|-&#Os$~REQ$0W)AG~46FeagJc60"
    "gDfDoJvSauE5HOJ)fcVph26n}KdodUEIbw~qf&?mR0?X<Q<<_7=fE3KC2(7OODo!5<PNA0Bm-@jIL{9TR0)!UmSmD@_6Jl8T_(U#"
    "A<##$8~%WbVOt1Wnu_+3Ayr6id-0IUp$)}DuuZl-piUsKv(?=g1<rtafyu)_3xa2I2h<4?#)irY@X_o25rfc2lOAFLbTQdRGy;ND"
    "U<-;cKURtGSUSTnxW#=!KzEb|v5e?}{Yfj3N%m@^4t{iWY7QP($()=7%ZSA9O}Ktu<yrB8w4P%`+~<g!$dw9m_6eRdIB=M5Q;p>%"
    "OqS<ZGTg<X7Z{QGLwR7-P22`2HX`{4u<#^2Xl)y8&x(wQ|DjB@_rdkdC;%0Hqzsi_L)AcQh!aVO``rC0O=KsIT{G(7Xhxd;N+;@c"
    "bw>j;)5;ddfgxRSBrCGl$Z-QRdgE9=T5XkhYMnDOBU9T^WX66toDb~tYza?bMv)xLM@x3%Fg}w;L0%fsaV<MOlS)BOq~x4WnGhT8"
    "e&jfC&ZJGKF`4B?JQ_pFL?P%=OqinOTp`i|?GpAeRd7g*ozQa(X^?|C&@`DJ1co%o!F(tW(Dlt|5301H1HsjQh%(+3wNTHS5%sX#"
    "f;F{$rqS|LF0;t$q*^tnPg(qs9|#;H%HSat%gg4{y^<4+<WO85)y)I%u|C@~BK=vhNFfIXFCuI^uN%?ZVZqb6FzK=l)x4#!WXmxl"
    "><~}f%}j}>TCKpotWvTt8RAYRFPmvb5co17?FaJFs$c>Xcj0b2XJevOKx0Rd8Q0@LPMW->9mQtC4hM38FQEVHn2AFkNdGATu*RhN"
    "K)tu25q(e~fZ@6<#H;mml|uQcgsvhj2y>XsSODu}3q!dO6ZK)s0dB5LY%;|BF&szb8&LoaE?QPJaH|_e!I*D1tukjB-kLO!wtor="
    "1)5}b=*31<K~IkA<RisCBiD#hIEtP6F`+^&$F<F*CJ;|~&Y@+P_Xj7jnJK=^s8BVw%_JtP6uQD>jgia%(#cnaj4ju|4kI(J!9Ii="
    "KfsQ3M23^DS2OOy*i22rK7`q{wxO!sgv_HAnHCRFRNoq_*wlJFCiw<0_sEDuV>Js%PqAr@NkJ=5<<b(itCWEU7XSzvOa!uxNHPwG"
    "#7-nKuOy{qGOK9%5LG+iwL{a{VADfqZvk#2XQLv}!}wibZH9)IW9hlnV<Jdz#hY5A+FVZ$CfX<G0cTdWCXzUzUUA@@oP+jEKF8O_"
    "Qx33xDTqyJn%fy>AD^BF5D<c%#y!wei9^umej2+oY%)4IX@!QwQ~Y4Yi8Ck%<wqW1A?Lj~9X)XRGl+|CPug=j&iv_FP~dBfd5-%K"
    "4Xg?34Wz&7N6KqDzBRBe=uVVzJa1r4KpeD3RXM&rur_#Ma*Q2Y1M3C6!Z9KO4Vfv=Q#PO(S}`^W@82Zdd29#F7};o8gk+SXuO@?!"
    "o{lJe?D6c+Pir0x!G)Pn9sSvA8_}NQn@}ZlXwNP49AeeXlzR6j!+tv+If*wajyEeS71jgHRo8?{nQvFyJi-1q$;Y}XA1ath5B4p="
    "g|*`65JG@kE={Uj_-}NkC3bEz(89rivY`o`W0pl2VKj1)yU2t#GQ}O{BlP|AJ6Vb78{klFpKHdtX@Cw|BT7h{s>wQnxR8}+VcKVy"
    "yJ^*#&=7XSM7B++u)ge+l!ZbZVk626+Z%9@OIe|7iNFQoiEPq`=P3;CY}h1p7}Or1AB;FZydd&J$*4)HDp50dh0(i7KBNUi0j3EZ"
    "m1eFDHhDWH^wd#&$Qd@6e2q=$BDvd;=-E69a+vy{919I55bj6O%7SpxF;AYUM;snyj%And5pmlIMPxfB6p>`DRfI+1X0F45oq~U="
    "RFLC^Ce(|h`%sln*nhrGlTA%c+Z3FkxG)54noACh4$FiB*;Q!`fUFYPZGja@P2?5!sNSv!!q9}`IEoEbeCS3-VkO5h(J4c8JM|LI"
    "fV^O5T@x0E+z1xyyRi}f=_sCLZ7`ud5CW%3RdNtBP2r>)_$IYRGd8S(a3T)XOR5ZJ6qa=Bm0M_dL)S)@sPXhFeq3N22eC<&ay(NU"
    "eRdtkq>ed;AL&j=?TjfHDEROrlX~YE)?Uhp1;VWJ&czlLZ|$6_!1qjQq@#J_LluOcNo8~dJD)pKfo+@AOh<6`xg&K7O(loraq5BR"
    "aT0C}G{Dw0D^n%}aKh?@CTEWL%SQI-_5E*u;CQ){C@wQRz8~Y*UC)V5qRmV&>F`DdmU9xdW`;-J$jCd5k~@mYWm0cO@++DH>DwpK"
    "c}7^|Z49mOB#O`gkB*Y-_#_(92#dUpK?fn+7>|%I{`i!vVQ90)2{H%Rjz;23JOGCVhvykt@NhlA`>Je_zA&8Gv5Z8I+QV*flC8L?"
    "(srrUI<4xgZY6_M=y$TH%C(BfyOEIycprYM6x{&yc48CisUI`^7S3h5u@Mu@1WSKy6C8StGdzsAg(kQH5K(rX4d|Ob98u1z)n@I6"
    "M&e6-c*3Y%Qxy;yF}8X$M2X@*jA<kM(uOO&V5b?f4Y!2+Yc0M65Npa>I%VSC8fo#<FfgH+jPcfvsRot_J!OP7xJ$Kt3j}ipG?qE$"
    "S{oO5MoNQv@`esu_qf57Z37C-6l>aPGg3WZg0r2&=9$rG#&~OQn`fKXZBWs>O4TqnVeU5EkpcYq5MdaZ*JlUv)5In`H!`on_Tc6-"
    "kuJ|n#bO_JT6+`v=Cu@-)ruuAgv+3!+*D7zc}$zZnOF&;ClUkCyk<I#owiu{`&SPL2B6k`p@8akr)#RWEK<xj01rt9iNsX+RAmhb"
    "-Jm;qp%+ak%Mu<cOt6Ha7f>)llawnsiQ?}|5EsG_!}%Vx4-+jA>Uh4ZN2Jcd(TXeF{2=OJC^*A&BWplg<=9xr;d**C!11gB*f&>}"
    "QKO$kn&(beaZZIFz938)sEdGmv#nFyqe<gxlN|BwrL1s2#eY(MH%_Paw3%={7TbXzmk7rS<q!)Vw4`=md9K#v@WtBquTf_Ta<W^o"
    "%m9xS8fYf`W_>buXxsWw$$_Z1Msa&q2XQeTfCC=j;JQI)1k(`26>?P=#epdeZ~%GE3PnO28j^AP9eG%-p}z#N_tQ@s`F0#d4Rxe;"
    "*uhIVUb-oa#|x#B4K*@l^C*-Wh<4~4B-+qoN)3Xt*s&5utA-Sl3BSD(PP_Q~dfTe9O6QLNkTXx!Ea0SCefNE&xhG$=N%p0`6neUZ"
    "C%pmfK7D{v2-(|IC38^cLg~T<1HIFhVd~;xVxV>=uAK5oyd4xgmKJSg8#y}yfRe_%0yA!?buoWU(|nzo=9OfD_ZXajggdy4a+<B6"
    "Py~L52jN*>q@gC6WB~q|729%K(?Etzm8~a5Db6JrrlF9vPS3J?V%kDjHc7>u?kw?<Yjv~bec8ZC9tbi_LXpE4w|qeO6i)w0<{zei"
    "ZPRTE$PuxpcQ<%Fxxinkj;wA4(O{AZiY94_teCFS6&=Rd>a;YvMp$U9j5Q!eNE@T&67z{{Yb}rs%kzz@31X+|HC$x1cymF%0aht("
    "*g#RWcMn@Y)e_7^mg63yHMSeLfV$w*vY0PVlaCV50x2g2J9;=#Rd0ekmLxbv86>)>eL*-u!f6tka>mD!$NtrDPC;_$^I&=|vm9on"
    "#J+}$bTl6d-K9GUQE`Bd92lswwuMea$3Amh*Vl%)?JFzBIbw^+5f+o4>^T=h5*<ir6FmmupNS!Ygy+s7aisNJ^AOua6)i6xBo6?4"
    "!ow{g(+tUTB<uk#i+nDA_!!xda088~pq~7+Pmyhfdc5LzrdmdFR`ZxvGSWXPqP4P^^5@C>@zwav-K$^6x9=}+uE%d*y}5Y*w^wg3"
    "-(TGRB7U=a{!qKA8=28<hF3=&Z;@q2&mGJhLH-1Wv|5$QfNCRvFGb@)w?}boMrl=YLWcPF`k8U1()OeaoDzonpn|)o%=J-sM{5PX"
    "*%3h<z-v;5@YrMu-xF{>5Y8)yI*|Htg@7-yZ$v9e%i{65#_B?*{!C611xxXRg1N=>bW?sv3y5Irv5;fsUB%d1k6b%$-+dpujuF+e"
    "NS6tKrKm7pEgKTq>u;KW%8Db`^`zPs)A{o}Z^o933)3-T>GWktP3IO~n+XMQ96t=Rj?Gcc9>sp-n$QXcc#_)`TOAncBezL4r*UbT"
    "oEg3nZ-X#t9EK(oLwlGhJ0nyP2M$0pl(POShk^{o+;c43jZCP8O!#UKkj3Gw&+Y_80^Z^-Uo{QL_5$08gCu7R(?eEW#hPxAeE=M@"
    "mw1o98Q16l4tAz#dvOQs(K|U0-PsFH=$lXq2XOGhB#yv}LlbJ?04`)xsoV$Oj!a1X0US^eM}vn7I@pj$i{(CCP$Ppb6pmwgu@S|v"
    "HwQaHA+7{_Fx7x8bY#M^;&?F4fDF7K65=F;gQ*6B;?*R=u^h`9Ofw(@y8_|Zw&j~p40tb!t4IT8eM~+AzyoBGbBO0o8neWT0~6X{"
    "KeEd5a9kt40?!>&1@RA6vVLqQX&ay<jurTwardXAjPHOjFJr-2kz<(<_B|;-l9~$uf72O)svK?K9z_>;zL7A&9@L@bTR6)23I_p<"
    "_yAug%`eUvi_UQ(J2D~3dsCJUDncMSwvpiF8pfwoO+&v*k4XDgjpF2Y{vearYvjWYEi)2+Bp*`Vy(lzd{vXSSlnOv$_Cq5P0!BKm"
    "x2qK-NEqsLUBN6=Xw=+1DqN_Vd_c(G$ByrqPz0Q%=A$>SQo7AMK40v|o(aKMvsyo9=@Z1@YH`3|&U4ApIQ|Nd4~zxnX;K6JH<-C5"
    "#9zg9+a-H2SV-0Rt0zx>C<T$>j*BDPgc{&?tkO-gNH&Q)aI$KEkJ|!IJkHgzjRXSu9c@7U&ysX~Bewje++c^r8fUp<rjbB|RZJ|C"
    "PXIIIgfZ{B$C&ZbM(IR83&JjHiV~zv17<rGK2xt>C>%N6bU!g!)xQ@w`h9*h-E9-70SZ+9fuI<W@K-@ktT@Cp2w;xNui*@p8r2;;"
    "fvb;$@z2OtRxI=E;Sm}0LPt+}o|7?oD_OC)mo()NoRcf@ZoISIAkdA6bM95Wohg7T!CI`(xZbnsW0y5G4y4=yMtRCWii~4R6C}!N"
    "mM!Wj1pMaDt!Wn%Kw@OfQ?UqqlAaxDy&~sf3^4m}r@}Dyd?Sjf7e8Q&i_c-ZN^H43G!|9Lh^xMSAxF~Wq#&fi88OC=c;*&qy0hf$"
    "Ihq&u-S>JdKJ3jBVG$GVdXqjT+S>x;9=-vFl++WfuB_o06+Deq6S`nfW?C(-wyQ~)Hz*(NDIt30<G_3{mb~t(yMyuoprqTHxNwYe"
    "k(cF%?YdFGAR_VvC=^g}eWq1~K<ns5TrNfo_l-WiVaYj+C5iStx_^(ke#fxr9L9}893I`=z8&9A-(KF1-`ri^Tu<3vvn|6Sbr^pt"
    "KYyD-u<<>ftI_yh<2U!n<?Z<0&F$Uv{oTdg{rl<t_4{|@H<v$MQW=G9SPBngy#Z3b&)VlNGAx&eaRrDLV}`dt+|=-_0t)QhN(^?D"
    "*LBV+q1e7ARL1YWZ!OPT(n&g}kV=NmFOHIvPrv_OoL|BeH|PF2yHw*&upEgEDL0RZ1ZmqR7H$q_NWksZY?d`NjbbQZf+)x~v2$9v"
    "-iw{<@jpiviPC9>dM|Edm#k#TtaNi&2C^A>svJKo1LH#cqJ)1vWCc!}!d_nd2N?!}CBqU9@gO;4up5HraeD|#Ql~tF5K3SS%hqn`"
    "%L<whN;!6WTU2R|(}J3yxSUgf;oE8bm^gkgihKTyXr!KFSW1sspE|W4QgLz@qno?Gjc*lyX2sSZe=2@w+Ke{GBh{ZGi)MxU7#3S&"
    "HH{x)>;IptYaOxktD#e@uysJ4Z)Jfpm@@J$tIgo~6%kKjWiSx=5Tc$xN{G&{#ZI-rmK>d551wd|t&?hf3u~lGwR)=NbZj20a{KT&"
    "ax5npS@Fn;?`-Qw%l*-^eo*}}|5m5*{0gtnBGieqzX8RD<4CmW+9OL7iu0?s7g=l_>GSI*9CkGt|Lfw--4wc}rr0ty`gZKA&oS6~"
    "&epfeWa~+vU+!C<Nj7asx8fgt_|LAoI!4NISgcSk+m+TmzE~jXL^$p<%+D?4XIhvWpEhZ+;9fFv3!6SVXl3u9z(#!-BD9U!7O}S="
    "IZdfS7?q>?fh!K>t3oygy7!|{uaFG~<|He`9ug<EhWNT?dfo&l++x(A|2uY|iQ?d_s9o_ofQWl)g(Fzumfz1E6xZ_gV*0@t>268j"
    "Mu9Qmfz0_9$17nkW|Fr?8d47^PkjeC3j_;UX9a*`xsEZhku_mZEHwcu#YR{>$2TSp9SRPD0EWJ?GcLbhLhx)0EK_JY#uoCM6#fcV"
    "G*iQyDrD#01-NGdH#T<gd-F#V+2!0K+lwt@ve271Y_~u#XxB9+7rnV6@NC4+LsSLdHZCtm^9IP31nBCR6EUW|t}-lKR$4^a1X$_V"
    "%n|nDC^RR4ds3GWk8Thfl&<}VbBh`_5$ND{Lr{mN0ys1PLz(bob1!tv-6VVRk$zfdP+k0#6^rr-=aU4nInmgc31LD{mHUkI#)KU>"
    "uq@z*AG3T>rG)KiTSE&3ez+O2?gqi&VgVn{NGc+0XsLh?x5q$4u{*Rtz=vz9e(?g=9$FNTJdert0?#>z5OA{#UJ8MK4jGU`HvstN"
    "4CkNbBBKnrgUf(6)ZPmsXJ`q~+m=1(y%<;sWXo`C)`_?fqM>C$?_Bm`*B)9HBnR4=5K`d`EftaxF3TX%aEBHRSrpQ;T783&t3A0u"
    "?Ld);nv1ACzXR?P4#fsu)m{+TLkkC!Zy!wGa)Y5IgxFBito*<oS~RxBhXN6Nvy>3~Jlo)%QLeBP7Ggg&PJFAT<jTr+WZ<M+Paw;V"
    "lO*HFoILDH95UePlk9@`96vHA42SR_rP1xco%lmAK)=!;6xW;>$V8%W)XEAI5zv@AgNazJxzg=X1@IdA)COhY2sSX9icqMrZB!r*"
    "WTc7RqD#@4f*uXajFi(Tu#L*dfoxE!NO{{}=7;Ayv4Ad!GY;(dtddhf7iEo5<Zcw5ReGxO2`W9v4-X8A&-z)yDyV7C&=N|6_2^qB"
    "@UETAoD~|hv<_usU6rd6onqhb45tl;;iH?B^X<r_%pA%`YeNEqfm`}3;s6MpNkH)udy}5Tk|#4V$dG*TF=1`*xlU+Ia1Lfim9-*J"
    "DCTYA(3tc9?$T_#Kr9Zx8v2BTY!*13ho0HCc&dbr@X?*Txe8Fv8}#3SyrhR1HEEFw!<W(sl~f+nLYQJp7^$!U&2GEKq@`RZ!v9j$"
    "1<D@g=79w33pQXXx0(iK8MQE1*c8XrYcvPjTz>s1&ZIY~Sn>g)(>gCp^;&#W+VJFhvAKjhqWn^X5oePDfSQIV!$`k%3+mT#Jac|N"
    "om7MiC4-up$a&-f&8>r^yLcAPaana}U;zxCc6zRUgq*%YKcJ=&EFi;w&C2qFIH42#n2rW-JHn^c4Yfh}P(W%iAd7_u*R&a8Lk(iG"
    "y~q#sDA?&45!B0k+bRN~LRi2X2?9q4hVf6%4Sm@U$r`X3x<*pqz<>_N2ty#D>hD~T!k}*@C=!VcfwcfNV%AV&R%Bb9JYsw-6PO)T"
    "Aak;73_;nGqQv?GStiFgMbJ4HMX_&2X6SY&;N?xSS_5>6G@=Qz2*79Nh=)wy2Qupfk!wa+6o?AGmer~)jWYZ)pD&RHcs}*y@_PEy"
    ")#cBByF(RX*`3msq1m~=elxzkySTiD-p@GdM30X?p)w}S*iqu*N(%)4ShV0cOD=LiSedYcigpNT%9nTK14RQ2KbYi+azJQ(Bq0(_"
    "zmW~c4s2h;0t&4jJaD5tk)BF?AI2GN4A&J7{A78B6A_1z?!&}=n9u=Gsit+2&8ZNbE_QBOq%A_s_Q=!wH^UwrkbIKXBnUG;fQUns"
    ")SDJ;n?h`JeBagmmVPX-WXO?f1&sKl8L(n^aLAN!un`|r7f;LgA{~At7>bX!Tn4_|K?m-`MxA_AgH_VzN>lYmXyZw=sIsEMU|EyW"
    "wShd#u{~olV}`Y6E+$~``R1g_1bck^5#%QdqR5z_nP5&~b=tt0^(}GMhAg*T&od{MCfIAHk=lSl)=yMlYCTXYd`acLd0sxT2BDgu"
    "4f7!M$0Gd%I1WKCq1=OsteEH9MT#(#^Azn(`C+oc>j^Rg8caLp(V4h`<Nd>*HHNkIWaPOxqGu*~(&6GlBRt^|L0AoG2C1VynPr>W"
    "+G#i$hOUQn&7aY)(e;t8`P29{y1^e^42ogshDi5B=W0!A5nzBZ)s8@^YwkFLC=d2`CyS4XkOwFPnAkh**=E?`{#S~CwyLc+)fV|n"
    "i;rnuuEnM-w$R%|MRe{74&eTr$f5xE1jliIE&!$)nXt~tM7z@2Il*b&pA-I0L_#uuOg9kDEi|m^CPX6c2oRxX661+`A`Pi{*llMN"
    "`%|qLv_|&NHj|WO{^Vs0nR(BjoyOPbn_2B9qincJ?C_GW1qVB=u-^*Xa(W&nf{^`D9G^(M`g8jt<jjfgivHZbjJh!E`P1!9|7PU<"
    "0PF(iN6T+@G=H-~Ed%7L-5a~VOhM6nvr6G5WQ1>4C%nL{dm?Y8KeIbn1ZphqRF1>_HierQzZ(C1@#eRw;QsR0@ri8G{=6Onrf?z|"
    "+!pnA4bdp^UdDJ@r1>UkfdFEXp+SH+m4F@2Nk8e?<p8odlwDHSH`FQBDs~3dCj4#LqVc@QTg2S8CKC3L++bfJ4r<bldORc#Toi5V"
    "DZoKlfOJE(NS+C<*gjHTdb80C)oD5$^_0h;>_9b@><8D8&4)dt&^sWvhJa_!c2<Fvro+c}&v#_SjU_IH1%+@r;{l@K8Ck-Yyc9C%"
    "$BUd{DKqJOQ_f_zuqg~n#Fq(1-j#GUzJq?35!Omk(9L#spuc;b;{AF1IxfFs@JR2^SIW@wZ&at2^yY2H;K@Fep?wDgyt}x)yS%u%"
    "`VBCtQB~S!IC!6%3=p103?zzrYeL3(&U^>NT;t~)XC3*udtRq3gLl8Z8&9t;f4RIn-*JZy6QQPd#qT^Pd6%8G{<ICBX}UMCFL4Tk"
    "BN)AjFxRGl_=3rkqMRlV4^{dACi7~WZ!)r<#g`v#l@v)a&#?RpOT}9-SM$vl|E_v}zySJ_D{py*r8v@CNHa;qsTo!GaF=6KJ2SFI"
    "Z^qYmw-;B_cegjcUc#xn5f1Uy#k=YK_1~{={&|gE5`k9!u(wNm(nb8^etbWEJ932&Ir{bD>hkTyo$%p*y15l9_}9zvKc_d>S1@?c"
    "cOz}w;Wx#74BTH{zrX+Kr^`2&;!#X5e!3gO65fw*f4zVk<vsRexOzWE{4K2E{rzj9n{bxEKF7!jZQWDoy@*+oL&8$XocLwMXpq<^"
    "$W!ZK>Zu$bKUr4ge+U%Dj&z}P`Z6NXqZa7~$5T;J?P6Qu+&9{4H60EMRVE)&aGe6%H7r_3u(B#);&@rXv^U_%i+!^Ka|BQ8Qm9aD"
    "C$u`1eeoH%c>)YSC{abwLN79Vj3q;|M_pWPbeL6uXesGa<1*9(D~O)}s?|?&nh)Yf2OJ4J+dIv<K8PtSexPw9IN9BfPIIsiVrtF)"
    "U)_Sh@SQL|%_%>KH61L)bz;ZxNmtsrb=e><sGCt|E40c0B;V}8-jBS4#4X$K0p{{Q=$0a+Qf{hitX+r`=NS#_Uc<wiZL?f_3tU-x"
    "S6hE}7RfXAA`0qdA*|KB85Nx;vlND-PLrAo(`V1}JVC3f@0mTSP~HCAprWp`LbgR@mDc*-9z%f!MQ2XxsX5ufX4d)fUEeo*V86l-"
    "Lw0a4O~Z#BI%d!ASNNb##1?pmI3t1C<BG#T{}dkp`gOVjc)2+p*gv<_<OF;0h;3F)TO=GGR)h=yWpz4d;`4DPOfG<?S?0+DLe9j7"
    "&Ev;aGDo|8kwO|BqV-bg)X0i>Vu~5EN4?k$%^u-{n1I;zZ2K%;djZg*{~<bvmv$w)FT6~%=lLL3T3#9e+IhBR*d#uT8|V&t*pjp0"
    "c#&`R)aK<A%w!WMA<tGB2VF4MBBu~?5;d)=vZ@(>wsuC%EDjs}20u1?lG~m8d}j*-E?rZ$L6)3hHoSy<E*lj95CS^ciOn8nwL{Zn"
    "R(?!d#toa5U|aB={+KNmzzUdwMYh_m1Vhl8Vf09gKjbYOWk!0C<_<ka%#+~&1DUCjorQj62WAhokeciP-vfa7>H)O?S{fyd0)QOD"
    "jgf~_8Yn%w9bdft4TDPWZr<NX2yy3|V3WTchwA#SXZW=9dnw|B0TyAPma|Qg!3P<@gw8)1v1}d&!u2zI&^J$|j6}5+A*usB=rTl="
    "Ci6!GurnO*li+YAOb+>LD-_`}(|rhu2Z7rGH#>>`M+DuEeb?;4KAaym6hAtsvHig838vsZ73P6hPZ2#H@n36b_^unDQ+Pmv524l5"
    "d<in|9}~JzWsYEQf$7-_Yv!47S_B_3-UTLmiYpAfU-c2WHNXZcBFIb!9o=mAW&?o*Kq!(qPi9qV1E5}KIk<w48GP<}x~aB>$`@Es"
    "ptaN*;D#q-P=FNR5vZX!PZM!(JSO>ax_L~wUyi1Syo`)s+hL$t&wI+&33*c!JVMXa!g2$%h66@k=FIk^SPx{lXJf4kfF*eIBEx6D"
    "CvVNZ1y&fDm4wb@mi2Zf6LrYxi<oIbeY<AQ`~h6F5W-^5GJC$Eh(=r?$!eB8Y|CxkQj+w<)=9My3O|3AGcM+kZOKW;L(UW`X~Nhw"
    "d$#+rwN9QH$W|O|$Lz^IoU?WhTjcn$>C=2DKj~_g3Qu4mqx5lLnLgTwvLxX*TJ?mU6}#s?>HvdN$Ut}$diJ>wIsX9<wqBodj)Sy@"
    "sV6B8UWr2AKKHo>%rgzeI*9!E+$S3mLxf=rrluN_<9VTH`fL{;DA|n)kSF3W#jYnH9+#^FZS+6O!ef=W4Z%iZ>o?GsrMU1bb`y{;"
    "n3Az;nLWkr@HBge<S9=ZxJ^pxX61y;6Fq((*^XoOBp<|5H;f;+USReNAH>e?<Zpxp_YA9MsN02VjWW;Kc~u8!1z|1dvwZ?*Zk1FY"
    "5YKLkA=$|C4I9;mak3Eml4n>77umLJ_9!353UuK-6GqcIc_5B-^zxxOdPvp?q!$`Kz=v^>6|xHwPp%c3J-JQQA|m?HU4SFucA~Bl"
    "5Ht28!-fQv8$y|)jJzU}+qSBkwk(FVRmLc2UjSe>dStooF(m~Ls9EA;c|t&gbV7y0BTRMZpvN?-iob-RW7vYg#5Mz$Qs9~D+ZD<#"
    "Z^=WI6fIyg0H%&@vqu(Ksf%P?KVsA=UDRk0Xt~r)$;C)&A}GY?Bg65G7lL7kuFxr&gHY<^_5E+7ku6lL_#Z4jhI$^O2lo(eF0bF+"
    "-w94E!p3xL%W%Bz5N2-f?|_?;<1;s*=Nmq~2XKR^h|PnFBj2#f#jH*j^0z1(QvqPRr`uwoaJ=DN01d6r_BFwYmMCF!shua0*>|wQ"
    "Cj`?6p#coyM84VcdmuO9p|FMqb{yZZ#c&`yHFQ1j)&!l>qM>=gW?|Ku75cGj`UD@uPInZL?>P1m2N|>E$%5MyI*_M`p@Zcj1OdzG"
    "Ep*^8)r|tq_oR44t{rJJ^!9v4pW~riF2Qt_5PDGABle&1sHA`kn<Y&)ljh?(qNL}+uxG~@PYgZ0Kx{*m9e#xClg+874FpWfI54>*"
    "VymBvxyo}QTJ^_Po^zonJ5Cq|$E=&j1t$xk1+;153S0kAEJv1wnwah+WMBmzO}JrMv4$`)n=SuI*btt};715U?(BopYNzH9_8to}"
    "EUl{aY}~bqKGC1Ff^TBeK=&|D!UXiciLIhpS%i2C7AqXG$o0O7{UY9E`C4rBY|W{zk@2~i!<TF^vVW)oCJ(7PYQmkf!aj^m?$jb%"
    "E>jG*Z5|;PFN=``Uo`Roa{b?Z?}nX(Xiu^L5lY2j8;&1A)0`eZ%G0DyW#p(;4;lo%V{!}j=LLe)I|Z^{r-h?0J3Gb)fo4-67Ar5R"
    "6bfgi>&NGsYOT3#9ZF@r$mG_5Y%ozYagqf#L8Jg#y@Z%R$=rU#-hhwPv$)~v0}q<z4DJjA`87QURhs@MRc`o9T=DA2;=90wi9(Yb"
    "Qm@l_w#??O+B0E@*G<SA?2JTCF_E!}dOO=NUH}e5wD#VWr>x@8Mz<`5?U-!=ncpgt+Cv=I9CHhcbeRA$uuO$bZnv{8D(Dv-7lWiB"
    "G2ca@8H#qb{r&f?<vH_nExy{$UiDr)*0BPacJ))9e3}*zm4fqFO>3dERta4f^@PH8gaXlP*+-K;+oGehtvT+!X8aqSG#T2g*`orB"
    "39dpacCkj*==T0~@1-6@uwotZ#T8cb>8336mfog6$Wj%dX0zopO?x6Nt0_$e75A5}ti8Rz8m<Q6gw`mZ5ZEUetY!iVmoY;jkjDKk"
    "3F*dGb6ba!#lMOh4+Cl&z=ThP0{AomG>-Ax(e3!n&Gnnht1<S9kFdOK=jr(U$z5LGL2LK>i(duNHy77$##dAR+r{<U>Fs#@>&4am"
    "#of5Yf@7A2+spTVpI*Fqb8~+U4ck9mUSC{Y{(Id1)2UY9CVhGRvsmsw?k{g)`&%z`pTL)wFFeVXk|$UlsG=0cI%Yg5f+Hj&1B*gG"
    "p~08G4a)U7I07Jd+~ZW^l%fpg@llng#^6`J5=Pj}Aa6xY&C_*KgA0K<qx9if&LC!_&h8WV+Lu?!)1v7GK<DH!dw6W}U<Y!mG82fW"
    "n_=FD_dwOKVi<*DCI_;szFkJRAnelCVvMfSYGOE$|CP}Bvc;BBI~GtpU-Ld6$gu2dRyiEBG6`*p6xM<K@gZpD_Dw!K3C0-U-|qiK"
    "ySMm}UlbkAksO<ZzB!b{)X=?p5I1V0`|r@_zv|#ULi9~`?lPDIRml^Iw;~t43J-G)JrD;m+4|;H_5eBo{Mq$m;!Z!>Dy}EIkgxMQ"
    "&68PYP~%iwr4T29X)zjG;fr6euF7Qw#rxWw&C0L!fW=CM))5kr@la6mHJ@Se&R40>M}%0l^zOETnM!iRr~+qDSd_5MQW)X7Ddw3l"
    "fuuTauPi!}ChKMLk{LdEo@{HOtq_{2dt~;evk<y&Rtlq2DCMfCr%#W<8D9t$!V@7_cay&kA+MuXBs4TPxRXp{H5hy`5GJFT{bbN!"
    "doGAR(b_h&ttm{`euuC+7pV%|b;hKG@d01n{!(o7qrKTw&v21)hGHXA{0nHZ(H62NEuQHGhOGE@ygF&f5Wanp&M|(a4ASN-LNsLq"
    "HQ9TtuomAxD?gEGPGKS)kzl*ts5eRRYu_(2pv7m<*YrSS9ZpofC`$$B;?!->XObb_vQj5r2OI4}uDRQlLD7CJ9Sr#wDDi=i<@0U("
    "ll3pm=TTE5HszGT!PGXVpR$_fOm46aM$?FqeF#M^MN&ol%7iJa?1AQ4V*})Nr6%Y-l%+VTp}x$6h)QaZE6b|7nd!;jj=F1GkY_8@"
    "Wl|&$I5k5^4x|x}Uv7&sDSJ&imsPn^JEBThJio_EJFm&CKvHB&zL9G2EV1a6AMOgO+bf9H^;l+?tX%aAUI0GV=DDx&efWMz8t=LE"
    "QW&Vh4_LPqApfbc2B^*~PahJY(&2$`^Q6-5YXHO0R_z=`kt&&0;cmSvI66^V3#0E6-4%Tf&rUzO)2IzrBSA=H(m>X(G2|Glz<N8w"
    "!IOc$Soc`Ea=55^nI~-~d0wju7s$hD%RHK#Sc_O<YhpKu%(hYmgW#<Z>`IOIiyfO;CVuZ`iui;d(sW%*J>RN3fD4Ycm)q><e}B2S"
    "{rmWCdinNh+{Mj3o*xjCgs1;v1`AUZSplJXPG)lePd;$RF|!ji*%X_GRe2=UHQ-jXZl+XAUL%g<m)9-`<D4K(pDfnNI%DI$Lcrop"
    "m944JBP+Q41ZUqCV8v*Q^!6iBr3=baP~~TWm19t7*|F|k_`VHVQ&ozoGT5oO)zm9~o38w+J8cT7+KBZ@w&LB!FUb?Qb~<)z<R4D5"
    "WlL`Jlu-jPV<qkx@Jgs~9rZ5Ch_=R;2o{SZ2M|DQ*IIwilV>>m7~{;yuS?hBu0eK~;Z2pM9n8}xID!^Mb+skn3L$q37b_A=i;r1V"
    "7ONN5_Y%zwj1jNsHk`Mt+X4Yi#WhJoC&?00)-=hNIE`RF>vmAMOXRO<B@Ti(r^$onZ@{U5(><-9#Py+uL~ymO%*cX_cUN)%kwpra"
    "ui_2qYFdle(^l!0QeDC@*ku2K;qF)O?&4QJ<mE#*To6Skqj`OQ_3DSsr_CQdmf22fL%HC8vUPJPr2(|d*W}I3FSaD^9lPG+iC;_F"
    "?y(EcF#tmO4x#Xcp|RPv8mp)i7i4Dsn0-vCXZTTg(|J~W*cpd#$fA!Stc;`X;DbX}T)-X2n<)<$3>YWd05<hOD2G~|s3X_+6*B+W"
    "ic2`H^5<7VYSX&!`K`RBa(SIR=cTyV3*lkcYPdW^RM2E5?Jq~$lcfPrE5D3?SS`8(Ru5TmPafzNORFE|$cEQrE4D@QQCN4V&<iTJ"
    "P_r}WcrDS&L;yy>`%5;Dgf`mafeX4Uv<r^=$qsV1vABja&lSE4>;gv`I<dP07yKkt2+TU}E>;@yt*RU?yfKl|<Q%{1zov7Wz!C{R"
    "2fV_qxR(~sln{ZdSNY{MwNa&-jy~@cUX><%JBPu((jtZbr?9g;jB>Hj9*gB8ii0i^NM*Gnmk120G&4U<N(w`J6gp88Jo-D6gZq=)"
    "7Im@&ixzP!sYDj$Vd9aSy!r9}fli-wx^p4LrzQ+3e>i?S*U^cWoV?mOz$ad7@<Vs(;>p+eV=c_!WHtCoe-}Tq{;MSr%cfPC+T~D0"
    ";Bv&Y4i&^riWH{Jj;N*rGnH_42rW`<>RpQ?9*Psdd`1!9am5GNjHc91&SLCNff!;Vx<ebNP&ycFGlUnF75q5rbhF8E@`N1!wxihN"
    "le0p#&{c{+y@Q^M?OM2<pOul}_My#3TS9_?cj83>8G0K|A|D(fk%<+_2G$e{Zz5T*!F?j9zpS1oUl0$wr$KNiD~82GJceYYq2bAi"
    "<T9p0me}OYl{0;R@ymF6`F8r#cszY`@$T;ac0B#nHgJC&z(qK1jXHgE^UJ%N>+$v7d*Y+#W~lf;D|nDUt6TlC6y|T9a$cq;W7O_%"
    "Jsd;U>2E$9LpJVj0q+E1>gN>%KBk-JHKh1B>9xn=>c4WV2U({yAU!L#+2*+&E!aSve8PoORG29iHUIET@|NV2<$}GoZdf9=PDPRe"
    "^AL7f=H=6lFB2a_SNf{7?czyh7#o3S))@o>Uoinc7U}{K|F!?B52Q{|R6zTyE9Zj3ok9n=nZ>!PF&@N)HNs1(9w1^&*;}3y(S5A;"
    "{zNdqP^UTf)XMigVwo}xp{UB;tl<lL$R);2g|HS*7rG8oAyv9*jcEbNfz(Pz-eTv#FPE*c-JGbPG)rss=md4_;1SP=lI$RYTDa(i"
    "o%=H7)}Nt}igHAZFd}<f))~!V!t6A3!S-5f^Jgkcg5m+#TnJ2ISPE}dELH-_Pi6Ii%E<aGQ?DOa^#qt~Q56$S4Uvw$xVyv+!e4Rv"
    "uXW&a%zDUFjw)Q8C#rB=ix2Go#e}TlLLYd$FqRA+98O1-YU$>&Tuk26=ik#Sqo30a|K(i^U0#<scMwkG5Y0ZDK;tlyS(Zam@-fXJ"
    "4aN7A9#028BwP-F$k)d%T39@b7SbC<_k?WD;hfE(cJA0UK?OEJD8abDT6TTHj&cO^uPsE$NiG{~<6~C>^svUNR;QF2_3!}_U=F{|"
    "Ri($Ti@&ESqREA*g8>Pd9&u`f*H)_tZ6RI~4~p{MlSMY)*zO+&R(Uja?~gOC-9P8jgQHiDwUX0K3jP=TtEthYK8<4+^|kMnHBs}d"
    "MgZ{L^8MW<2-R;}(^*x1;CXAZcB5?tyW%bL+?`q;BugxxTRf7YhG`MpPMcxH(juKp)kLKmy2<ACqaVl{48}AG6MPEbBDCxh!~Qi4"
    "&eB%w_M&LJ2)9G%l2{UxZ#a)bAm^Nc==fDV*J*oA14*kqOU<|GcpRTwBem>PA`GvI)+GW}iQu}T!mFURrG~^2J3-HQjD(dXY=f*q"
    "YgmR=x-0dMrk!WYbpAXSb_njTx~o12!+Q>eLIuZDCO=(VUg4litLsUAZlm`gPvsd%m8O}K^e7mAoEgNh1D)!iV;0Q=Zn;IAtjwlG"
    "!kK<ZHvQN3S2&%0Tum*T<rE4vNfe>Y8>2Gy1faYkYvfpHhtLdH-%>vaOdj-Hu4E)YMq)0v0e2@)9%|gyX);q4H~_nYC9+1AFWw|Y"
    "rByawZD$mnRMjLC?l~5k%NcP!_Rre{VCmAGX1wD!p$|JdchK>hVoxR7v(P7qb0o_<w>(Z+7MM-2ffeDP4DEo!iUy<rW|%-XZ~|qt"
    "Xd|nOSvc-1wHq%!5Hnj5wvdFisU}>ZAs*J2%VE=}X3{R?H4O>Ifh^o#zZu`&U0mWMU$5sbfDH|ei*m#h-vQ!0HoeVh)7^~bQVO0v"
    "K;Cg1^-JQ?_g*l`mx065i=D91sK}O-DEpjy)MiS2WOM=v>+%OhP+S+m_zvPi_>eZ|oOKeQZM2@9K5*N>J{`n|d|0k?uHC5aUwk^}"
    "+m;qXe_%oQz8k}EM2ix>ZpYNMr<OBK>gN?WG~#+Bgcw9RP=8~~o{Y2|k3Z-`+m%gTL{}JPHO{tzs%LN>5w;NR`?@O{ejmCK&8=)<"
    "FX|o@w%EZtsFBd<n&V~$F5J3E3!to01&cIIKABywxc(6h8sQ&PaJpMV@`O_XP3iUI?&4pg@xR`UuiuZS@bA%1wyM{{g(oNWdy%#?"
    "qg<YrKl4k%7fGrYyyiZOcdTh^hAszQ+fUo%({*e~p_T!VP7^QmP>El6e2qPp2>FoR{@)!gJO~qL#~BCb;k;E9qsTrcaIPI~0!kd3"
    "WWsOagfvJQ!{JSqultY#lf<!i;Q<{ziP)jat*}fEI*n*fSqptdxIp&FHU!)dNOBP%w?wf+z)$KyMNDI{D{gqQeG!_V<zdhBq^?y8"
    "lJu_Z>#yhHODDfvT;E?@O)uX5_x=03U&hyWs1)oVINdHC$f43ywspGWB)kP^l*SRTUBDh{<ANOH7DtTOfq%Nq3DII;$Q1c8jLFlK"
    "kq;@3+bh?}Ut2gzVSKVZscIWbsXy90!)ppbq-6p!riqm8pKe2d2{mVMk=FAnTSG&R>@I@3@@~_y@m<&!3V5J3LAcf3*dV1GxDPLO"
    "HZg4nqD<Ke(}`%X0gB;Vn_<kcL@2q@lg|A~M?51I3fwbJ^pi6UVN<L$M?Kd}U2wH($vEoGf>7}Qn%v-rV5hl`zFF_s%DtCv2Tk^N"
    "!}lq}C(eXDITv4(6)Dc7P*;5?j?;VXLJue~O&L`BAPsjJ>Vk_m#d-=IugCSxZiwT_hH=@DpMDbN92vIA12o~Y4=iCh(dzr|doMJ*"
    "F^(??lytqz_I1uFeo~TBl&}{wtQ@lSvXcJ0;W~87b4Lr=9Llms=MI^8B;bu!uHQ1d6|SqgK$sT9`>B$GtsX*-FVO5Rprk^djr+OH"
    "5KnoVhR*b1o4r(Q!*TXy_DM}-c>ohkHqjEI4|Rdo&xN?R;Iwk^p9)%**l?=*9`Uivz_^^Mg+haEMiAdyBshPTiX=&$fmN!j%BUaN"
    "wVEA|-UoW{RV@uZnj|3{Yy{a+DW1u?T_53@+=ia~NEq{=_Z~IR19B0y0xhdmeu&WO!DY%r)pZKpJ;|D2kjkenyxmc|({KVh0z6WH"
    "A$lQ8#;_@V@3lWvrO=iVT!U1Eu<K-q9{MgfoTDNv5!>#)O8BEJ6-n}^<Qdb}Y4(vD#9C(rEn1i<xL_frPG7h?LaX=U|FVFO#da<z"
    "Z4{spV{9UZ5{_nT8`t7H$e8K0TpYC#5`KZNhZ;TgU63Ow2c16G&n@|{5UF963{8pH9hKFZo>=jeaC*Pi5J(j`U9>mbvW6jS?=;MP"
    "ZkJ6{4$igY(8P)XT^BGLct!|go^y0d&QvL&GL}Bhsu$e-zH3&+rl>4gZ4nok=Y<*#MzrJ%KNFu=BWLvb{<lAN>KZ@40W_(EXIkvQ"
    "mr;2I;+gC9ak5cHmQtjDZ4-Q9Q*EK53={z*#?{YA`^6eb7pohT6mI|dc@0|sc3c0JOuL=3i7!3oBbgR<HRtn%9;<~F#mfNL8-(D!"
    "y}r1bzP`D?etU8I+mtIhurcD|SMkfm>&vUlyWghQH+NHLM*nT}<|>%Jxe=fI*WKv#)y<o~i}%02z4`n2cKYk(_@C2XF5mxharfqL"
    "r+*-@Hi9^71uv)AHJM<wYunW|vCVBnCJbFNLx3%vR3Bv8PrCS$`sCgH>#NK6up^o^fAp3d%ZG$^zPz}*yt(csjE>)y+?^t1t^RmA"
    "jjRN+f@($<GvPF;dctpj=6G*M+1qF5$^XOD@yUHH|JUJ7gE!QrRXsr#yr~qtFAJ)#y`-e|&6U@@&D@3$ytrL0^k#g0cPq5nyW5*z"
    "FW(A%bo1-@_Uhu@^#1zq*Ej#X9_TqjAegRnR7*)#(Gn*w@cG~4Hz11B_jebFB{syP_&R?(=flXR1q4I!Y10lyQf{})Q%jW_+mo-5"
    "hdPY~RK>x)A;VI7L&WiG-)<n%!NZx9(r(mv<4AMav#kG2sISG;CNLijxi4x*R)tW1J<o*AY#BQX{rCv~7;HbZ1`>6j%HmTan}PE|"
    ")i^j_xs2Y@K~b}K<w%l|)APsF4j#e0V}|+j?0h_0_WUtr7Qe{6jI?){H+*7MJQlU3$5lF7r$z&chyq5<JUFP{eQ~D7(c93A7w^T{"
    "8YkTs`J`GEx2>MA0XL=F9Jhg#&;(NhtC4hVP3_EIVFlGEkThk~2083Zl{lnmi5L=&h8_SDr<_87%pXXHxa}1X7Xg4l|J)*7t;@|0"
    "yky@s%V@1i=`=g9YKf^7W%4WJXx>~+h3$HM2j=eI$G?eRM#i)?vgA*#P|k9#bh})JI|=7_a`*b;`tQ^C7uRoJ-~3C>q!A2t|0IXk"
    "m^?1F+icY4sf2hPm&2(>$GQXX+-qM1J;n=bN;>4^TM`k<Ii!Fg>xQAJbvoYEi6sR*tAt)E@#kl@nz1P#4@_vtVCt@`#|WLiT)cRv"
    "YztybA=2A{{*aH{bOi2IC5JNE$Vo{WUfDF2EkH6qVrAs#&*iKCdL`B}Qpv*+fA{ad7k}uTLPzh_@k~>q`PJum|F17{ZeqKk?rl25"
    "zz7z&az8qN-D+L7)H^NDTDn}qDVVU5s&~wnBR7&-DZ6#gtqw)b|H(PCSs@2gh%H`L$tvyYn$PY_GEJRyTCd6vySYzPzL@;w$sH-Q"
    "mw^zrZe_Opyb%N(Zf{x&x)tjTv|Vve&5S>~ySV*%e20#@;OnQ$pYKTn``WHJ2Qjip0Z$(%ydY>X&OmLFUJ{5y%`@BSBt3NH%j83<"
    "9Y<O@jW5PZK8f((s;7a<^X}6>Y@JQrX)`%_QDsZ=@uU-u%mJ|pWs1Z$l*<wRF9Uei)~JmO56D6?h=mcI(Le6T_xMzt$m+38s(3|N"
    "c0x9xa=VsKYMIO`9>XHb$ff8uIj?#<heR*1W5qqTPgzeDz-`Ua%~P5d4ah2DdQvfq@Iv$|Bd^CUNbiQTUF|v|mYnv}<BV57fsn(1"
    "RYzzmFW-c3X3vD);P+VE4yA6LmcFbB6;oko<d4oY$U_&|o;=WMgp`IWVZAhC2kgk%cbz!K7zeGa%dFUF84M^rT4c#X0evKM-ASt`"
    "I?Rx<JeQ$q)AzK+_Mnd!$uwEO&6ZRMT*FhJsaSPVfI1}TRNd0_PDr$Z4M(@sH@i@o1qbvY4rx))wRsMc@-d%Nhr;OQZ&z=oH@9!c"
    "w>##OHyA4bCqSIs{q}CWV-5A#lg708Ix93mlePuCo$-6&^;}&4JXZVTnA)F1*g_B##nAh|UEmHmy?01fc*$#~Q*hse>k&+k(gwl{"
    "{Pp7M@-5~TRSrj9kne5;gBNdw!So(phD_wI<J;eK*Rk&o?$35>f=k1qOdGww_-QN{gWSvuKJ794Ni#uM@v>#B)Ek-~H5Y4_>l)J@"
    "5S36fYBF&yCam~J=_iO^iRBp4NhUaZEXxlg1m74jlSWjR3D(e57|C>wws+n)v3If}o%aun?@?l8`<7X?t5o0u&671q0_qePwpgS>"
    "$K*?@`GvbycwqU&v7BIJ#UsbLv*I5uOZ?{tDqs0G;ZvypF{1p8a_ef`>aRKQ8ZY6e%Uhw9UXOpi6bftn)6MO8`qS;rFGf`8QW)&b"
    "$XzwT3z|^mAIam$njtDCbf8Wb@guUjg`^@uCOTyME*D==Mc%5gT|POQn+#SD8u2vBScqamt!=$mjZM+A#U%SrTDQAzl+>%J280|I"
    "P>Gk<CPWQ<M3U;NRk|+Ih2)!Xj78qz-4ygh+&<!tytta;)pR?(yvEyfr2gJqp06*j-@?pr`4*YP=zklSSja!_Z|=qzmiXy*{Qhqv"
    "`u*m1`pfw4LRbeEJA4~>0Ig6H4|yf`seR1pGT_%oga8QOgEwH+y@osN>hf0@{w^$%_m^nJyt}!&eDmAqOWM3)c$NAPV}c`RA3kjZ"
    "d&;2(IW(@pjRb+_OAPR+0HCtz?-GAmW}nRLRELAK5H9c>@yvJ<F=PfF$XlJK*&1Tw3mj+em|MU6YK1k7!5|vHhE7}4pf3Rms0Yl1"
    "VtauL=q>|LQVamlG5*5n_2U-bbn7XF&5eCcZ@#Qi*=#F<&H@m&i7akHzi9gAM*RQf^~K%I?TFqGjP9?&`v3X*^54Q~cz<_scmLjq"
    "e|bF5{|GOp9NWDTJpJwF=I`&xLiil7#`YJ52VZPDLM2{cYazh=q-}8-+^$1dcz1jG>&4x;mAII`xwyW#{jK3^kF8NNx_R^@%}K!G"
    "teuXv0H8=({j7jS+A3^N1-`znZ+s(^UDd9Z)va<Z25g{8e)7^Oy#sMxZ38?bY{YUHJ6Xx{K1NkD`JewC2^O}bi|o-XS&Sf3B5&Cp"
    "$J!s5C5KqL{ADcU=a+XHU&gn}ZmRrb%pUO)(aelO^BVyzx2k%K<~#y|pn_>^UPOL6V{;9y+VQluN}3Xi{Zgp(iu4KlPV18#1;_7#"
    "D5|F34+-~kvZpMrCMPz(eAjBpCO5(I&~pZis3-sP`*)05`TKW2+TUxx{qJdH#I;)==tQDc$)?Iap;lp2<|N|a`cox{+<GLQ58}Sa"
    "m@~67QAP}6MJ%wu`j`7VisnHf3JvKv!8YyB<N7hdINf+U{ug(y{B(8m&p*z+cYKy!DAX~tLqSW8DB=1tOBxyqfMq*=V4`;M6dQfS"
    "@|q7U09Ki1qZ3ZP`3g<{8(1Z`#~WScDV}9WZ`*T$JgG=nbsGxP+0Mwd?;Q6>KNkP_LAA{N`$Up;7`qKxg;bW`wqQllNIOXNcL5De"
    "?e(7A@C7dK#XnACQ}4?Sq~J@e$*VS9oY{dlhTqgVd;9MX&r2b0xbs2g)LX;LIo85uH!7v}SmqG=+hQb0`Crg5+<}X;t-uHaGFF=v"
    "JYk$?$^c7LizY+Mk{j`O^vVFgA0t%uNR=fB18$pKX3K|_Q*|gb2wnp=Wl_Ng52^3E*1jR?ge%dIhWffqx?0ye^lg(*xmh%#&H$(;"
    "*S!L8)J|urZ-|(T;n4^|08K+YPo>zgo@Ua9`4Z2bm3-3k=MS?7=!iwhfMpXteXPpu!=p-(+4h}nX~{ly_nLbeSb<t17!h{pu9Ec{"
    "$9su`&>F9R=W+shgsbt}pU1b;+ws--*Nf}B(apQ@E%_xF=y-kq8-PqV`TD>!xXlM~dppJ!cv;l&5~qOj-+3D(qfKrwodZ_jn9|8$"
    "uG%N?kMn0Jpoj9R&12e90}tDzT67S}`feTZQKwMj1UNBNKuO23x!t(V1&iI~-KL|f^xhA=CR&kn2_wllETQ`rSo1dVEZeY@9zxqS"
    "ENG}zS+UDr7)rz<>a6$$1LNF`Dh@cF^PmAHzrBAA>AA?{hV*8t+QQgt&oX&I6eWrR9KN|;XPaQfES8^dvrTIDaF9wao0q1IDDdr@"
    "mnA^xZHvVVc2{I@3rIGbN<^OTB_&dpAF2Enm#suQ-g0nSHn@K*7ndI+Q4hBVXw7rZ7HXSE^PWEgZyp20;@&y??p;l2T-XsP<CpE)"
    ";Rhk97=Zb6TYw>%R5iA`&+{z=z$H*S7w~VyVMf#qEWBthJv@fIc(qt0IaCED)%+21U2?`kE&In7JUC9yKSqpcNB0zhV~Dc{7fr<%"
    "i2vH+zm9xFgs=z*<69~{>11ewIPAar76Y#MuP;sLhAP(IQ0%=U!T8o6AWROoj^M}SemL>ea;?vewWOJ-5<O1aF-#B`+!q(72?PzG"
    "#5`fNXo55(9p6F;icIc;8#0;2mPc2CEhf~xvVAJ>r@MDb-zAH^_bfHDl`{?KWyxHova(!*%l*r7r*rX7h;u?NIZtM3o}&jX9LVR%"
    "8*4|~g{LJ5*FzkbULv&oVG}BN$_f*VJ4PkeH7FR%HN4n`HC>2bUtaB7MCWka$nbi<OccTSyucZBrHKFG*ruQCM<F@F*u=kW*AF;("
    "f}U?#CE~He48u4KzPi+i%-RPKwM|*(t*Fa-Ei~V}LH1W8cp0U8waqsf+|QsjmRZhSMb1|#N`qP&3NO;-j$+kJhPff`X>Qtk9sSrw"
    "N7KVu<xqGdj1=rB?|OAFe>IQSY!!%3Va9pOSWko&gRzT*U$~}OTpEHjsd(&LqOPSHV}tE=#m5m>CcE4L6(E(E@bn3s*`5^-fDelt"
    "v}Y8M1z>5Lu{S^ZR)ahB%%Muf$~viQh3vrHQ>vjCz-%Faftb^s?oHb|jteMd2c5WP=N4uS(EZb??mU(N7jiY<+j_zT+)TsIJ7xVZ"
    "l8dJ}#`_bDw@G*&LH7WQH?Yva=ovOy@(<-B(oG9cDm7&VApEV}qqhAb(MBn78ROzB(xc&Xu&118Wz2H^DZ9@EAQEWSL|<l_H%f8F"
    "MW+}QU5Inuf(6i|+jhP}SpR(eRk~b3uExG2b{VaImeeU7He1)v#+ld_Eld$|1Zj*n0|UrYXgm6`cVFbAIfGyVi--MO%v+%&1Se#`"
    "_yrEoP$li$R)VQh&h%#s1Sz+BQUzCvC%wM(Mle~S@wUp=-L6yHJAxc_dTXa!HG#`week-;v*ByR4)$1P3kLsl*FVoE5CS*l5)Oj="
    "Ae<KML#oC}TqLgkZ2e=&OZSKqd#VFgMNE}Xt2!7ug`c0<@;zwNzRR;C%lG)ol=SOH+USj9>*IKz#q;H;i`N&e61J<AZPL{)zUD9Y"
    "r`v=HWJv|dbQ{rLHM)b5s~OgI@FIOm_cEG8SC^nu-agGQV()OeZACytx-LJM=tEx4gyen5AiX9iw9$uNxK~h>)y?riMW!t~e3^r7"
    "A-BAZ;Gy#(?X;Ats^F}%A;&u~XAl(~g$S-!&As>x4-DQT9k9=xd-eJ%DY7O01?C)QYf8||0TdWnBQwp_cOZJ1IyTUM`8N6by{=-u"
    "P{=5kZ@c>P`!ay<q&%;BAKhWuMvA?>P|^L+>{MIWsegR`9l)BelJ9=}?)}}x>#H%iOt;P(=$`xc_<H*Gp0i%>Zf}0Nyc$n`wZ9Xe"
    "!6)?HkN@-Cqp;Y-X9Ir`r-|pLv2Xic<hk>O?YawV?j)|`ECPR-&XU<I5}(C!Xq1N)ADmnBRJgiJH%LNjF<&O(Jf1~q>N#obEM}2-"
    "z+vL2vA1x8V4nEFJdQjs4c)opB(WchFRYxTIRfg71K0PRrM-yV)U)UI!U<x37Q2DHbQZo~HT9RXXy$q|H%YDKeC{Q-??i4mi#^+2"
    "F7b<nEWqf*)p{eo(B{8nuwFNGJUjLy+wsJo#m<8nt}444Cc>FL4<c)^bOa~fGMZa~P`ja@dS19##IrzbS>$+DoTT%m_}``F%|dUM"
    "1c9F<?i{`w2BrM?o#!snc@zjo)?eDG_|!6TgK)8&x#Fbxt{d9c(s9L!`rq%rgWzo~J_GK;Lt4FB37wG<;7a}fzM_W0kLjy<n&q#`"
    "<tv#&euW|3{{xxD|FaRMvN(RlE3DHLXGeS~LEKYCviSBQg(8E^s7i_tui|*~_(~`n2uupU3BFc*P4m@@EhntMe+9XjR{#L?N<*=0"
    "kwA1HMQ4?K%2wNz`mTlgvRBKhT#4^XHm~U2pa1gz{r>>qCErv"
)

HERE = Path(__file__).resolve().parent


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("ascii")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as stream:
        stream.write(canonical(value) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def read_json(path: Path) -> Any:
    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in values:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=pairs)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-c", f"safe.directory={cwd}",
                                   "-c", f"safe.directory={cwd.parent}", "-C", str(cwd), *args], text=True,
                                   stderr=subprocess.PIPE, timeout=20).strip()


def source_identity(cwd: Path, source_only: bool = False) -> dict[str, str]:
    if source_only:
        # Only permitted explicitly for isolated runner tests/source-archive checks.
        # Never presented as Git provenance. Outputs must live outside this tree.
        rows = []
        for path in sorted(cwd.rglob("*")):
            if any(part in {"__pycache__", ".pytest_cache", ".git"} for part in path.parts):
                continue
            if path.is_symlink():
                raise ValueError("source symlink is unsupported")
            if path.is_file():
                rows.append([path.relative_to(cwd).as_posix(),
                             hashlib.sha256(path.read_bytes()).hexdigest()])
        return {"mode": "SOURCE_ONLY", "files_sha256": digest(rows)}
    # Full checkout and unchanged tracked source are required in GitHub CI.
    if _git(cwd, "status", "--porcelain", "--untracked-files=all"):
        raise ValueError("tracked source changed during diagnostics")
    return {"mode": "GIT", "head": _git(cwd, "rev-parse", "HEAD"),
            "tree": _git(cwd, "rev-parse", "HEAD^{tree}")}


def child_env(folder: Path) -> dict[str, str]:
    # Preserve genuine GitHub identity, never borrow credential/token env values.
    allowed = {
        "PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "HOME", "USERPROFILE",
        "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "PROGRAMFILES", "PROGRAMFILES(X86)",
        "LANG", "LC_ALL", "LOGNAME", "USER", "LNAME", "USERNAME", "VIRTUAL_ENV",
        "PYTHONHOME", "GIT_EXEC_PATH", "CI",
        "GITHUB_ACTIONS", "GITHUB_EVENT_NAME", "GITHUB_EVENT_PATH", "GITHUB_SHA",
        "GITHUB_HEAD_REF", "GITHUB_BASE_REF", "GITHUB_REPOSITORY", "GITHUB_WORKSPACE",
        "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "RUNNER_OS", "RUNNER_ARCH",
    }
    env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    temporary = folder / "scratch"
    temporary.mkdir()
    env.update({
        "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "MPLBACKEND": "Agg",
        "PYTHONPATH": str(HERE), "MOEX_ROBOT_RUNTIME_DIR": str(temporary / "runtime"),
        "TEMP": str(temporary), "TMP": str(temporary), "TMPDIR": str(temporary),
        "Q7A_CI_EVENTS": str(folder / "events.jsonl"),
    })
    return env


def _kill_tree(proc: subprocess.Popen[Any]) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=20, check=False)
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if proc.poll() is None:
        proc.kill()
    proc.wait(timeout=20)


def invoke(cwd: Path, folder: Path, args: list[str], timeout: float) -> dict[str, Any]:
    folder.mkdir(parents=True, exist_ok=False)
    env = child_env(folder)
    command = [sys.executable, "-u", "-B", "-m", "pytest", "-p", "q7a_ci_events",
               "-p", "no:cacheprovider", "--tb=short", "-ra", "-vv",
               "--basetemp", str(folder / "scratch" / "pytest"),
               "--junitxml", str(folder / "junit.xml"), *args]
    start = time.monotonic()
    receipt: dict[str, Any] = {"command": command, "profile": PROFILE,
                               "timeout_seconds": timeout, "timed_out": False,
                               "environment": {"python": platform.python_version(),
                                "platform": platform.system(), "sqlite": sqlite3.sqlite_version,
                                "pytest": importlib.metadata.version("pytest")}}
    write_json(folder / "started.json", receipt)
    with (folder / "pytest.log").open("wb") as output:
        proc = subprocess.Popen(command, cwd=cwd, env=env, stdout=output,
                                stderr=subprocess.STDOUT, start_new_session=os.name != "nt")
        try:
            code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            receipt["timed_out"] = True
            _kill_tree(proc)
            code = proc.returncode
        except BaseException:
            _kill_tree(proc)
            receipt.update(returncode=proc.returncode, interrupted=True,
                           seconds=time.monotonic() - start)
            write_json(folder / "exit.json", receipt)
            raise
    receipt.update(returncode=code, seconds=time.monotonic() - start)
    for name in ("events.jsonl", "junit.xml", "pytest.log"):
        path = folder / name
        receipt[name + "_sha256"] = (hashlib.sha256(path.read_bytes()).hexdigest()
                                     if path.exists() else None)
    write_json(folder / "exit.json", receipt)
    return receipt


def events(folder: Path) -> list[dict[str, Any]]:
    path = folder / "events.jsonl"
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        # A truncated event on crash is not silently treated as a complete stream.
        rows.append(json.loads(line))
    return rows


def valid_node(node: Any) -> bool:
    if not isinstance(node, str) or "::" not in node or "\n" in node or "\r" in node:
        return False
    filename = node.split("::", 1)[0]
    return (not filename.startswith(("-", "/", "\\")) and "\\" not in filename
            and ":" not in filename and ".." not in Path(filename).parts
            and filename.endswith(".py"))


def timing_profile() -> dict[str, Any]:
    try:
        raw = zlib.decompress(base64.b85decode(FROZEN_TIMING_B85))
        profile = json.loads(raw)
    except (ValueError, zlib.error) as exc:
        raise ValueError("malformed frozen timing profile") from exc
    validate_timing_profile(profile)
    return profile


def validate_timing_profile(profile: dict[str, Any]) -> None:
    if not isinstance(profile, dict) or digest(profile) != FROZEN_TIMING_SHA256:
        raise ValueError("timing profile integrity or mixed source/run/attempt")
    if profile.get("schema") != "STABLE310_R2C_FROZEN_DURATION_PROFILE_V1":
        raise ValueError("timing schema mismatch")
    seen = set()
    for row in profile["records"]:
        if (not isinstance(row, list) or len(row) != 4 or not valid_node(row[0])
                or row[0] in seen or type(row[1]) is not int or row[1] <= 0
                or type(row[2]) is not int or not 0 <= row[2] < 8
                or not isinstance(row[3], str) or not row[3].startswith("batch-")):
            raise ValueError("invalid timing record")
        seen.add(row[0])


def planning_weights(nodes: list[str]) -> dict[str, int]:
    profile = timing_profile()
    observed = {row[0]: row[1] for row in profile["records"]}
    modules: dict[str, int] = {}
    for node, milliseconds in observed.items():
        module = node.split("::", 1)[0]
        modules[module] = max(modules.get(module, 0), milliseconds)
    ordered = sorted(observed.values())
    default = ordered[(99 * len(ordered) + 99) // 100 - 1]
    return {node: (3 * observed.get(node, modules.get(node.split("::", 1)[0], default)) + 1) // 2 + 2000
            for node in nodes}


def weighted_partition(nodes: list[str], count: int, weights: dict[str, int]) -> list[list[str]]:
    if type(count) is not int or not 1 <= count <= 64:
        raise ValueError("shard count must be 1..64")
    if not nodes or len(nodes) != len(set(nodes)) or not all(map(valid_node, nodes)):
        raise ValueError("invalid or duplicate collected test IDs")
    if set(weights) != set(nodes) or any(type(value) is not int or value <= 0 for value in weights.values()):
        raise ValueError("missing/extra IDs or nonpositive/malformed weights")
    groups: list[list[str]] = [[] for _ in range(count)]
    costs = [0] * count
    # Longest processing time first; ties bind node ID, load, count and shard index.
    for node in sorted(nodes, key=lambda node: (-weights[node], node)):
        index = min(range(count), key=lambda index: (costs[index], len(groups[index]), index))
        groups[index].append(node)
        costs[index] += weights[node]
    return [sorted(group) for group in groups]


def partition(nodes: list[str], count: int) -> list[list[str]]:
    if (not isinstance(nodes, list) or not nodes or len(nodes) != len(set(nodes))
            or not all(map(valid_node, nodes))):
        raise ValueError("invalid or duplicate collected test IDs")
    return weighted_partition(nodes, count, planning_weights(nodes))


def planner_identity() -> dict[str, Any]:
    return {"schema": "DURATION_LPT_V1", "timing_sha256": FROZEN_TIMING_SHA256,
            "tie_break": "descending positive integer cost, nodeid; ascending shard load/count/index",
            "batch_estimate_limit_ms": 600000, "timings_are_outcomes": False}


def collect(cwd: Path, out: Path, shards: int, source_only: bool = False) -> dict[str, Any]:
    identity = source_identity(cwd, source_only)
    receipt = invoke(cwd, out / "collection", ["--collect-only"], 180)
    rows = events(out / "collection")
    collected = [row for row in rows if row["kind"] == "collection"]
    if (receipt["returncode"] != 0 or receipt["timed_out"] or len(collected) != 1
            or any(row["kind"] in {"collection_error", "deselected"} for row in rows)):
        raise ValueError("full collection failed; inspect collection evidence")
    if source_identity(cwd, source_only) != identity:
        raise ValueError("source changed during collection")
    nodes = sorted(collected[0]["nodeids"])
    payload = {"schema": PLAN_SCHEMA, "profile": PROFILE, "identity": identity,
               "run_id": os.environ.get("GITHUB_RUN_ID"),
               "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
               "nodeids": nodes, "partitions": partition(nodes, shards), "planner": planner_identity()}
    plan = {**payload, "sha256": digest(payload)}
    write_json(out / "plan.json", plan)
    return plan


def validate_plan(plan: dict[str, Any]) -> None:
    payload = {key: value for key, value in plan.items() if key != "sha256"}
    if plan.get("sha256") != digest(payload) or plan.get("schema") != PLAN_SCHEMA:
        raise ValueError("plan integrity/schema mismatch")
    if plan.get("profile") != PROFILE:
        raise ValueError("diagnostic profile mismatch")
    if plan.get("planner") != planner_identity() or plan.get("nodeids") != sorted(plan.get("nodeids", [])):
        raise ValueError("planner binding or canonical collection mismatch")
    if plan["partitions"] != partition(plan["nodeids"], len(plan["partitions"])):
        raise ValueError("noncanonical partition or omitted tests")


def batches(nodes: list[str]) -> list[list[str]]:
    result: list[list[str]] = []
    weights = planning_weights(nodes)
    for node in nodes:
        module = node.split("::", 1)[0]
        if (not result or len(result[-1]) >= 16
                or result[-1][0].split("::", 1)[0] != module
                or sum(weights[n] for n in result[-1]) + weights[node] > 600000
                or sum(len(n) + 3 for n in result[-1]) + len(node) > 10000):
            result.append([])
        result[-1].append(node)
    return result


def assess(folder: Path, expected: list[str]) -> dict[str, Any]:
    issues: list[str] = []
    outcomes: dict[str, str] = {}
    receipt: dict[str, Any] = {"returncode": None, "timed_out": False}
    try:
        receipt = read_json(folder / "exit.json")
        rows = events(folder)
        for name in ("events.jsonl", "junit.xml", "pytest.log"):
            path = folder / name
            observed = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
            if receipt.get(name + "_sha256") != observed:
                issues.append("changed evidence: " + name)
        collected = [row["nodeids"] for row in rows if row["kind"] == "collection"]
        if collected != [expected]:
            issues.append("executed collection != assigned selection")
        if any(row["kind"] in {"collection_error", "deselected"} for row in rows):
            issues.append("collection error or deselection")
        for kind in ("start", "finish"):
            ids = [row["nodeid"] for row in rows if row["kind"] == kind]
            if Counter(ids) != Counter(expected):
                issues.append("missing/duplicate/extra " + kind)
        extra = {row["nodeid"] for row in rows if "nodeid" in row} - set(expected)
        if extra:
            issues.append("unassigned report IDs")
        for node in expected:
            phases = [row for row in rows if row["kind"] == "phase" and row["nodeid"] == node]
            by_phase = {row["when"]: row for row in phases}
            if (len(by_phase) != len(phases) or "setup" not in by_phase
                    or "teardown" not in by_phase
                    or (by_phase["setup"]["outcome"] == "passed" and "call" not in by_phase)
                    or any(p not in {"setup", "call", "teardown"} for p in by_phase)
                    or any(row["outcome"] not in {"passed", "failed", "skipped"} for row in phases)):
                outcomes[node] = "incomplete"
                continue
            if any(row["outcome"] == "failed" for row in phases):
                outcomes[node] = "error" if any(row["outcome"] == "failed" and row["when"] != "call"
                                                for row in phases) else "failed"
            elif any(row["outcome"] == "skipped" for row in phases):
                outcomes[node] = "xfail" if any(row["wasxfail"] is not None for row in phases) else "skipped"
            elif by_phase.get("call", {}).get("wasxfail") is not None:
                outcomes[node] = "xpass"
            else:
                outcomes[node] = "passed"
        finish = [row["exitstatus"] for row in rows if row["kind"] == "session_finish"]
        if finish != [receipt["returncode"]]:
            issues.append("pytest session did not finish with recorded exit code")
        if receipt["timed_out"] or receipt["returncode"] not in (0, 1):
            issues.append("timeout/crash/interruption/invalid pytest exit")
        cases = list(ET.parse(folder / "junit.xml").iter("testcase"))
        # A setup+teardown error can produce more than one testcase for one node.
        # JSONL is authoritative for identity; XML must contain every selected case name.
        observed_names = Counter(case.attrib.get("name", "") for case in cases)
        # Pytest's name contains the function, class components live in classname.
        expected_leaf = Counter(node.split("::", 1)[1].split("[", 1)[0].split("::")[-1]
                                + ("[" + node.split("[", 1)[1] if "[" in node else "")
                                for node in expected)
        if any(observed_names[name] < amount for name, amount in expected_leaf.items()):
            issues.append("JUnit missing selected case")
        expected_xml: dict[tuple[str, str], Counter[str]] = {}
        for node in expected:
            filename, rest = node.split("::", 1)
            before_parameters = rest.split("[", 1)[0].split("::")
            name = before_parameters[-1] + ("[" + rest.split("[", 1)[1] if "[" in rest else "")
            classname = ".".join([filename[:-3].replace("/", "."), *before_parameters[:-1]])
            phases = [row for row in rows if row.get("kind") == "phase" and row.get("nodeid") == node]
            failures = Counter("failure" if row["when"] == "call" else "error"
                               for row in phases if row["outcome"] == "failed")
            expected_xml[(classname, name)] = failures or Counter({
                "skipped" if any(row["outcome"] == "skipped" for row in phases) else "passed": 1})
        observed_xml: dict[tuple[str, str], Counter[str]] = {}
        for case in cases:
            key = (case.attrib.get("classname", ""), case.attrib.get("name", ""))
            kind = next((kind for kind in ("failure", "error", "skipped") if case.find(kind) is not None), "passed")
            observed_xml.setdefault(key, Counter())[kind] += 1
        if observed_xml != expected_xml:
            issues.append("JUnit/event identity or outcome disagreement")
        junit_bad = any(case.find("failure") is not None or case.find("error") is not None
                        for case in cases)
        if receipt["returncode"] == 0 and (junit_bad or any(
                outcome in {"failed", "error", "incomplete"} for outcome in outcomes.values())):
            issues.append("exit zero contradicts evidence")
    except (OSError, ValueError, KeyError, TypeError, ET.ParseError) as exc:
        issues.append(f"unreadable/incomplete evidence: {type(exc).__name__}")
    for node in expected:
        outcomes.setdefault(node, "incomplete")
    success = not issues and receipt["returncode"] == 0 and all(
        outcome not in {"failed", "error", "incomplete", "xpass"} for outcome in outcomes.values())
    return {"success": success, "issues": issues, "outcomes": outcomes,
            "returncode": receipt["returncode"], "timed_out": receipt["timed_out"]}


def run_shard(cwd: Path, plan: dict[str, Any], index: int, out: Path,
              budget: float = 2100, batch_timeout: float = 1200) -> dict[str, Any]:
    validate_plan(plan)
    if type(index) is not int or not 0 <= index < len(plan["partitions"]):
        raise ValueError("invalid shard index")
    if (not math.isfinite(budget) or not math.isfinite(batch_timeout)
            or not 0 < budget <= 2100 or not 0 < batch_timeout <= 1200):
        raise ValueError("timeouts must be positive and within unchanged 2100/1200 bounds")
    source_only = plan["identity"]["mode"] == "SOURCE_ONLY"
    if source_identity(cwd, source_only) != plan["identity"]:
        raise ValueError("checkout differs from independent collection")
    for key, name in (("run_id", "GITHUB_RUN_ID"), ("run_attempt", "GITHUB_RUN_ATTEMPT")):
        if plan[key] != os.environ.get(name):
            raise ValueError("mixed workflow run/attempt")
    out.mkdir(parents=True, exist_ok=False)
    assigned = plan["partitions"][index]
    write_json(out / "selection.json", {"plan_sha256": plan["sha256"], "index": index,
                                        "nodeids": assigned})
    start = time.monotonic()
    results = []
    for number, selection in enumerate(batches(assigned)):
        remaining = budget - (time.monotonic() - start)
        if remaining <= 0:
            break
        name = f"batch-{number:04d}"
        print(f"START shard={index} {name} nodes={len(selection)} {selection[0]}", flush=True)
        invoke(cwd, out / name, ["--", *selection], min(batch_timeout, remaining))
        status = assess(out / name, selection)
        results.append({"name": name, "nodeids": selection, "status": status})
        write_json(out / "progress.json", {"index": index, "batches": results})
    all_outcomes = {node: outcome for result in results
                    for node, outcome in result["status"]["outcomes"].items()}
    for node in assigned:
        all_outcomes.setdefault(node, "incomplete")
    unchanged = source_identity(cwd, source_only) == plan["identity"]
    report = {"schema": "Q7A_CI_SHARD_V1", "plan_sha256": plan["sha256"], "index": index,
              "identity": plan["identity"], "source_unchanged": unchanged,
              "batches": results, "outcomes": all_outcomes,
              "success": unchanged and all(result["status"]["success"] for result in results)
              and len(results) == len(batches(assigned)), "seconds": time.monotonic() - start}
    write_json(out / "report.json", report)
    return report


def aggregate(plan: dict[str, Any], artifacts: Path) -> dict[str, Any]:
    validate_plan(plan)
    issues: list[str] = []
    all_outcomes: dict[str, str] = {}
    found: dict[int, Path] = {}
    for path in artifacts.rglob("report.json"):
        report = read_json(path)
        if report.get("schema") != "Q7A_CI_SHARD_V1":
            continue
        index = report["index"]
        if type(index) is not int or not 0 <= index < len(plan["partitions"]) or index in found:
            issues.append("duplicate or invalid shard")
            continue
        found[index] = path
        if report["plan_sha256"] != plan["sha256"] or report["identity"] != plan["identity"]:
            issues.append("mixed plan/commit/tree")
            continue
        if not report.get("success"):
            issues.append("shard reported unsuccessful")
        if not report.get("source_unchanged"):
            issues.append("shard source changed")
        assigned_batches = batches(plan["partitions"][index])
        expected_names = [f"batch-{i:04d}" for i in range(len(assigned_batches))]
        if [item["name"] for item in report["batches"]] != expected_names:
            issues.append("missing/extra batches")
        checked: dict[str, str] = {}
        for i, selection in enumerate(assigned_batches):
            result = assess(path.parent / f"batch-{i:04d}", selection)
            if not result["success"]:
                issues.append(f"shard {index} batch {i} unsuccessful")
            checked.update(result["outcomes"])
        if checked != report.get("outcomes"):
            issues.append("summary disagrees with original phase evidence")
        for node, outcome in checked.items():
            if node in all_outcomes:
                issues.append("duplicate executed test")
            all_outcomes[node] = outcome
    if set(found) != set(range(len(plan["partitions"]))):
        issues.append("missing shard artifacts")
    if set(all_outcomes) != set(plan["nodeids"]):
        issues.append("full collection != executed union")
    for node in plan["nodeids"]:
        all_outcomes.setdefault(node, "incomplete")
    return {"schema": "Q7A_CI_AGGREGATE_V1", "plan_sha256": plan["sha256"],
            "identity": plan["identity"], "success": not issues,
            "expected_count": len(plan["nodeids"]), "counts": dict(Counter(all_outcomes.values())),
            "issues": issues, "outcomes": all_outcomes,
            "qualification": "DIAGNOSTIC_ONLY_NOT_RELEASE_OR_TRADE_AUTHORITY"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="action", required=True)
    coll = subs.add_parser("collect")
    coll.add_argument("--cwd", type=Path, default=Path.cwd())
    coll.add_argument("--out", type=Path, required=True)
    coll.add_argument("--shards", type=int, default=8)
    coll.add_argument("--source-only", action="store_true")
    run = subs.add_parser("run")
    run.add_argument("--cwd", type=Path, default=Path.cwd())
    run.add_argument("--plan", type=Path, required=True)
    run.add_argument("--index", type=int, required=True)
    run.add_argument("--out", type=Path, required=True)
    run.add_argument("--budget-seconds", type=float, default=2100)
    run.add_argument("--batch-timeout", type=float, default=1200)
    agg = subs.add_parser("aggregate")
    agg.add_argument("--plan", type=Path, required=True)
    agg.add_argument("--artifacts", type=Path, required=True)
    agg.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.action == "collect":
            plan = collect(args.cwd.resolve(), args.out.resolve(), args.shards, args.source_only)
            print(f"Collected {len(plan['nodeids'])} IDs; plan {plan['sha256']}")
            return 0
        if args.action == "run":
            result = run_shard(args.cwd.resolve(), read_json(args.plan), args.index,
                               args.out.resolve(), args.budget_seconds, args.batch_timeout)
        else:
            result = aggregate(read_json(args.plan), args.artifacts.resolve())
            write_json(args.out, result)
        print(json.dumps({key: result[key] for key in ("success",) if key in result}))
        return 0 if result["success"] else 1
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        print(f"DIAGNOSTIC INFRASTRUCTURE FAILURE: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
