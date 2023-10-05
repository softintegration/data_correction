# -*- coding: utf-8 -*- 


{
    'name': 'Data correction',
    'author': 'Soft-integration',
    'application': False,
    'installable': True,
    'auto_install': False,
    'qweb': [],
    'description': False,
    'images': [],
    'version': '1.0.2.2',
    'category': 'Technical tools',
    'demo': [],
    'depends': ['data_correction_log','portal'],
    'data': [
        'security/data_correction_security.xml',
        'security/ir.model.access.csv',
        'views/data_correction_views.xml'
    ],
    'license': 'LGPL-3',
}
