# baselines/

每个小队版本一张基线：`v4.4.json` = `{"squad_version": "4.4.0", "soft": 92.0, "lanes": {...}}`。

第一次真实跑（P1）之后由 `python3 -m runner report --baseline baselines/v4.4.json` 使用。
在此之前保持为空——没有真实跑就没有基线，不要编一个。
