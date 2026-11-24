        import {workspace} from '../workspace.js';
        import {el} from '../dom.js';

        const page = {
  "title": "灯组责任与保护二签",
  "module": "观察位灯组责任",
  "caption": "观察位灯组责任",
  "detail": "普通观察格由灯组领班签发；雕塑保护观察位需展陈复核员另签。补测不再作废待二签移交单，由该观测版本首版归档图谱按簇内格子是否一致裁定沿用首签或退回领班。",
  "path": "/light_handoffs",
  "columns": [
    {
      "name": "code",
      "label": "编号"
    },
    {
      "name": "plan",
      "label": "谱图"
    },
    {
      "name": "cell",
      "label": "观察格"
    },
    {
      "name": "cluster",
      "label": "关联簇"
    },
    {
      "name": "source",
      "label": "交出灯组"
    },
    {
      "name": "target",
      "label": "接收灯组"
    },
    {
      "name": "status",
      "label": "移交状态"
    },
    {
      "name": "first_sign",
      "label": "领班签署人"
    },
    {
      "name": "second_sign",
      "label": "复核签署人"
    },
    {
      "name": "first_plan",
      "label": "首签所用图谱"
    },
    {
      "name": "ruling_plan",
      "label": "裁定所用图谱"
    },
    {
      "name": "ruling_reason",
      "label": "裁定结果"
    }
  ],
  "layout": "stack",
  "view": "table",
  "actions": [
    {
      "name": "offer_station",
      "label": "领班签发观察位移交",
      "path": "/light_handoffs/offer-station",
      "fields": [
        {
          "name": "code",
          "label": "移交单",
          "kind": "text",
          "initial": "LX-024"
        },
        {
          "name": "plan",
          "label": "图谱号",
          "kind": "text",
          "initial": "MAP-BANK-01"
        },
        {
          "name": "cell",
          "label": "观察位",
          "kind": "text",
          "initial": "BANK-01-0-0"
        },
        {
          "name": "target",
          "label": "接收灯组",
          "kind": "text",
          "initial": "SOUTH"
        }
      ],
      "help": "填图谱号、观察位和接收组，系统留存领班身份与首签所用图谱。",
      "roles": [
        "operator"
      ],
      "success": "已写入"
    },
    {
      "name": "check_station",
      "label": "展陈复核员签收",
      "path": "/light_handoffs/check-station",
      "fields": [
        {
          "name": "code",
          "label": "待二签移交单",
          "kind": "text",
          "initial": "LX-024"
        }
      ],
      "help": "经复核后才能改变保护位责任灯组。补测后须等新版本首版图谱裁定；裁定沿用首签方可签收，退回领班的移交单需重新签发。",
      "roles": [
        "reviewer"
      ],
      "success": "已写入"
    }
  ]
};

        export const {title} = page;
        export const key = "light_handoffs";
        export async function render(root, role) {
          await workspace(root, role, page);

        }
