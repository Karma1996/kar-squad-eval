# baselines/

每个小队版本一张基线：`v4.4.json` = `{"squad_version": "4.4.0", "soft": 92.0, "lanes": {...}}`。

第一次真实跑（P1）之后用下面的命令写入（只接受全绿的一轮，失败轮会被拒绝）：

```bash
python3 -m runner report --records records --squad-version 4.4.0 \
    --write-baseline baselines/v4.4.json
```

此后的回归用 `python3 -m runner report --records records --baseline baselines/v4.4.json` 比较。
在真实跑之前保持为空——没有真实跑就没有基线，不要编一个。
