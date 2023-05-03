# -*- coding: utf-8 -*-
import psycopg2

import base64
import logging
import os
import time
from tempfile import TemporaryFile

from odoo import api, fields, models, _
from odoo.exceptions import UserError,ValidationError
import ast
import hashlib
import json

_logger = logging.getLogger(__name__)

CORRECTION_CLASS_SELECT = [('new', 'New'),
                           ('append', 'Append')]

CORRECTION_TYPE_SELECT = [('rules', 'Rules'),
                          ('file', 'File'),
                          ('sql', 'SQL')]
ACTION_TYPE_SELECT = [('update', 'Update'),
                      ('delete', 'Delete')]

SQL_QUERY_TYPE = [('insert', 'Insert'),
                  ('insert_link', 'Insert link'),
                  ('delete_link', 'Delete link')]

SQL_QUERY_TYPE_QUERY_MAP = {
    'insert': 'INSERT INTO {}',
    'insert_link': 'INSERT INTO {}',
    'delete_link': 'DELETE FROM {}'

}
REPLACE_POSITION_SELECT = [('first', 'First'),
                           ('last', 'Last'),
                           ('all', 'All')]

APPLY_ON_SELECT = [('record', 'Record'),
                   ('constraint', 'Constraint')]

APPLY_TYPE_SELECT = [('all', 'All'),
                     ('some', 'According to rules')]
ORDER_BY_ORIENTATION_SELECT = [('ASC', 'From less to greater'),
                               ('DESC', 'From greater to less'), ]
ATTR_TYPE_SELECT = [('field', 'Field'),
                    ('function', 'Function')]

OBJECT = 'sale.order'
FIELD = 'name'
FILE_FORMATS = ('xls', 'xlsx')
MAGIC_FIELDS = ['id', 'create_uid', 'create_date', 'write_uid', 'write_date']

RULE_TYPE = [('operator', 'Operator'),
             ('statement', 'Statement')]
TWO_STATEMENT_OPERATIONS = [
    ('=', 'Equal'),
    ('!=', 'Not equal'),
    ('>', 'Greater then'),
    ('<', 'Less then'),
    ('>=', 'Greater then or equal'),
    ('<=', 'Less then or equal'),
    ('IN', 'In'),
    ('NOT IN', 'Not in'),
]
ONE_STATEMENT_OPERATIONS = [('is NULL', 'Not filled'),
                            ('is NOT NULL', 'Filled')]
RULE_OPERATIONS = ONE_STATEMENT_OPERATIONS + TWO_STATEMENT_OPERATIONS

RULE_LOGIN_OPERATION = [('OR', 'OR'),
                        ('AND', 'AND')]
INTEGER_COL = 'column_data_to_set_in'
CHAR_COL = 'column_data_to_set_ch'
BOOLEAN_COL = 'column_data_to_set_bl'
TEXT_COL = 'column_data_to_set_txt'
FLOAT_COL = 'column_data_to_set_fl'
MONETARY_COL = 'column_data_to_set_mn'
DATE_COL = 'column_data_to_set_da'
DATETIME_COL = 'column_data_to_set_dt'
HTML_COL = 'column_data_to_set_htm'
VARIABLE_COL = 'column_data_to_set_var'

TYPE_ATTRBUTE_MAPPING = {
    'many2one': INTEGER_COL,
    'char': CHAR_COL,
    'integer': INTEGER_COL,
    'boolean': BOOLEAN_COL,
    'selection': TEXT_COL,
    'float': FLOAT_COL,
    'text': TEXT_COL,
    'monetary': MONETARY_COL,
    'date': DATE_COL,
    'datetime': DATETIME_COL,
    'html': HTML_COL,
    'variable':VARIABLE_COL
}

VARIABLE_OPEN = '{'
VARIABLE_CLOSE = '}'

DEFAULT_SHEET_INDEX = 0


def _get_references_from_xls(xls_file_url, from_index_name, to_index_name, sheet_index=DEFAULT_SHEET_INDEX):
    try:
        import pandas
        filename_xls = pandas.ExcelFile(xls_file_url)
        xls_sheet = filename_xls.parse(sheet_index)
        data_from_table = []
        data_to_table = []
        data_table = []
        for module_name_row in xls_sheet[from_index_name]:
            data_from_table.append(module_name_row)
        for module_name_row in xls_sheet[to_index_name]:
            data_to_table.append(module_name_row)
        if not len(data_from_table) == len(data_to_table):
            raise UserWarning(_("Data in file are incoherent!"))
        data_from_table.reverse()
        data_to_table.reverse()
        while len(data_from_table) and len(data_to_table):
            data_table.append((data_from_table.pop(), data_to_table.pop()))
    except ImportError as ie:
        # the python exception doesn"t pass silenlty
        raise UserWarning(_("You must get the module python3 pandas."))
    return data_table


def _is_variable(string):
    if string.count(VARIABLE_OPEN) > 0 and string.count(VARIABLE_OPEN) == string.count(VARIABLE_CLOSE):
        return True
    return False


def _get_variables(string):
    if not _is_variable(string):
        return []
    var_tab = []
    variable = ""
    while string:
        if string[0] == '}' and len(variable):
            var_tab.append(variable)
            variable = ""
            string = string[1:]
            continue
        elif string[0] == '{' and not len(variable):
            try:
                variable = "%s%s" % (variable, string[1])
            except IndexError as ie:
                raise UserError(_("String format invalid!"))
            else:
                string = string[2:]
                continue
        elif len(variable):
            variable = "%s%s" % (variable, string[0])
            string = string[1:]
        else:
            string = string[1:]
    return var_tab


def _convert_to_int(expr):
    return int(expr)


def _convert_to_char(expr):
    return str(expr)


def _convert_to_bool(expr):
    return bool(expr)


def _convert_to_float(expr):
    return float(expr)


TYPE_INSERT_TYPE_MAPPING = {
    'many2one': _convert_to_int,
    'char': _convert_to_char,
    'integer': _convert_to_int,
    'boolean': _convert_to_bool,
    'selection': _convert_to_char,
    'float': _convert_to_float,
    'text': _convert_to_char,
    'monetary': _convert_to_float,
    'date': _convert_to_char,
    'datetime': _convert_to_char,
    'html': _convert_to_char,
}


class DataCorrection(models.Model):
    _name = "data.correction"
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _rec_name = 'correction_note'

    @api.model
    def _default_currency(self):
        return self.env.user.company_id.currency_id

    @api.model
    def _get_correction_password(self):
        key = '{0:010x}'.format(int(time.time() * 256))[:10]
        return key

    # all case fields
    correction_class = fields.Selection(CORRECTION_CLASS_SELECT, default='new', string="Correction class",
                                        states={'draft': [('readonly', False)]}, readonly=True)
    correction_type = fields.Selection(CORRECTION_TYPE_SELECT, default='rules', string="Correction type",
                                       states={'draft': [('readonly', False)]}, readonly=True)
    action_type = fields.Selection(ACTION_TYPE_SELECT, default='update', string="Correction action",
                                   states={'draft': [('readonly', False)]}, readonly=True)
    contact_id = fields.Many2one('res.partner', string='Demandeur', domain=[('parent_id', '!=', False)],
                                 states={'draft': [('readonly', False)]}, readonly=True)
    sql_query = fields.Text(string='SQL query',states={'draft': [('readonly', False)]}, readonly=True)
    sql_query_type = fields.Selection(SQL_QUERY_TYPE, string='SQL query type', default='insert',
                                      states={'draft': [('readonly', False)]}, readonly=True)
    object_id = fields.Many2one('ir.model', domain=[('transient', '=', False)],
                                states={'draft': [('readonly', False)]}, readonly=True)
    object_to_correct = fields.Char('Object', compute='_get_object_to_correct', store=True)
    attr_type = fields.Selection(ATTR_TYPE_SELECT, string='Type of attribute', required=True, default='field',
                                 states={'draft': [('readonly', False)]}, readonly=True)
    function_id = fields.Char(string='Function',states={'draft': [('readonly', False)]}, readonly=True)
    function_field_id = fields.Many2one('ir.model.fields', string='Field',states={'draft': [('readonly', False)]}, readonly=True)
    field_id = fields.Many2one('ir.model.fields', string='Field',states={'draft': [('readonly', False)]}, readonly=True)
    field_to_correct = fields.Char(string='Field', compute='_get_field_to_correct')
    field_to_correct_type = fields.Char(compute='_get_field_to_correct')
    linked_field_id = fields.Many2one('ir.model.fields', string='Linked field',states={'draft': [('readonly', False)]}, readonly=True)
    linked_field_data_source = fields.Char(string='ID affected',states={'draft': [('readonly', False)]}, readonly=True)
    linked_field_data = fields.Char(string='ID to link',states={'draft': [('readonly', False)]}, readonly=True)
    order_by_field_id = fields.Many2one('ir.model.fields', string='Order by',states={'draft': [('readonly', False)]}, readonly=True)
    order_by_field = fields.Char(string='Order by field', compute='_get_order_by_field',states={'draft': [('readonly', False)]}, readonly=True)
    order_by_orientation = fields.Selection(ORDER_BY_ORIENTATION_SELECT, string='Orientation', default='ASC',
                                            states={'draft': [('readonly', False)]}, readonly=True)
    fields_to_show = fields.Many2many('ir.model.fields', 'correction_fields', 'correction_id', 'field_id',
                                      string='Columns to show',states={'draft': [('readonly', False)]}, readonly=True)
    check_all = fields.Boolean(string='Check all',states={'draft': [('readonly', False)]}, readonly=True)
    correction_line_ids = fields.One2many('data.correction.line', 'correction_id',states={'draft': [('readonly', False)]}, readonly=True)
    correction_line_ids_count = fields.Integer(compute='_get_correction_line_ids_count')
    total_selected_lines = fields.Char(compute='_get_total_selected_lines', store=False)
    # fields to be showed and processed in the case of correction_type == rules
    # is_computed_field = fields.Boolean(compute='_get_is_computed_field')
    # compute_method = fields.Char(compute='_get_is_computed_field')
    correction_key = fields.Char(string='Correction key', default=_get_correction_password,states={'draft': [('readonly', False)]}, readonly=True)
    correction_key_print = fields.Char(related='correction_key', store=False, string='Correction key',states={'draft': [('readonly', False)]}, readonly=True)
    correction_note = fields.Text(string='Correction note',states={'draft': [('readonly', False)]}, readonly=True)
    column_data_to_set_dt = fields.Datetime(string='Data to set',states={'draft': [('readonly', False)]}, readonly=True)
    column_data_to_set_da = fields.Date(string='Data to set',states={'draft': [('readonly', False)]}, readonly=True)
    column_data_to_set_mn = fields.Monetary(string='Data to set',states={'draft': [('readonly', False)]}, readonly=True)
    column_data_to_set_fl = fields.Float(string='Data to set',states={'draft': [('readonly', False)]}, readonly=True)
    column_data_to_set_in = fields.Integer(string='Data to set',states={'draft': [('readonly', False)]}, readonly=True)
    column_data_to_set_bl = fields.Boolean(string='Data to set',states={'draft': [('readonly', False)]}, readonly=True)
    column_data_to_set_ch = fields.Char(string='Data to set',states={'draft': [('readonly', False)]}, readonly=True)
    column_data_to_set_txt = fields.Text(string='Data to set',states={'draft': [('readonly', False)]}, readonly=True)
    column_data_to_set_htm = fields.Html(string='Data to set',states={'draft': [('readonly', False)]}, readonly=True)
    column_data_to_set_var = fields.Char(string='Data to set',states={'draft': [('readonly', False)]}, readonly=True)
    data_as_variable = fields.Boolean(string="Data as variable",default=False,
                                      help="Check this if you want that data to set to be processed as variable not fixed value")
    apply_on = fields.Selection(APPLY_ON_SELECT, string='Apply on', default='record', required=True,states={'draft': [('readonly', False)]}, readonly=True)
    apply_type = fields.Selection(APPLY_TYPE_SELECT, string='Apply type', default='some',states={'draft': [('readonly', False)]}, readonly=True)
    correction_rule_ids = fields.One2many('data.correction.rule', 'correction_id',states={'draft': [('readonly', False)]}, readonly=True)
    constraint_name = fields.Char(string='Constraint',states={'draft': [('readonly', False)]}, readonly=True)
    currency_id = fields.Many2one('res.currency', string='Currency', default=_default_currency)
    # fields to be showed and processed in the case of correction_type == file
    column_data_to_replace = fields.Char(string='Column data to replace',states={'draft': [('readonly', False)]}, readonly=True)
    column_data_to_put = fields.Char(string='Column data to put',states={'draft': [('readonly', False)]}, readonly=True)
    order_by_column = fields.Char(string='Order by', default='id',states={'draft': [('readonly', False)]}, readonly=True)
    order_by_position = fields.Selection(REPLACE_POSITION_SELECT, default='all',states={'draft': [('readonly', False)]}, readonly=True)
    data = fields.Binary('Modules file', required=False,states={'draft': [('readonly', False)]}, readonly=True)
    filename = fields.Char('Modules file name', required=False,states={'draft': [('readonly', False)]}, readonly=True)
    ignore_empty_fields = fields.Boolean(string='Ignore empty fields', default=True,states={'draft': [('readonly', False)]}, readonly=True)
    correction_insert_element_ids = fields.One2many('data.correction.insert.element', 'correction_id',states={'draft': [('readonly', False)]}, readonly=True)
    correction_insert_element_ids_count = fields.Integer(
        compute='_get_correction_insert_element_ids_count')
    state = fields.Selection([('draft', 'Draft'),
                              ('done', 'Done'),
                              ('cancel', 'Cancelled')], default='draft', string='status')
    correction_ids_count = fields.Integer(compute='_compute_correction_ids_count')
    appended = fields.Boolean(default=False)
    prevent_trigger_computed_fields = fields.Boolean(string='Avoid trigger dependent fields',
                                                     default=False,help="Avoid triggering the calculation of dependent fields,use this at your own risk,you have to be aware about the impact of your modification if you check this option!")

    def unlink(self):
        for each in self:
            if each.state != 'draft' or each.appended:
                raise ValidationError(_("Can not remove non draft Correction"))
        return super(DataCorrection,self).unlink()

    def _compute_correction_ids_count(self):
        for each in self:
            each.correction_ids_count = len(each.data_corrections())


    def data_corrections(self):
        self.ensure_one()
        domain = [('name','=',self.correction_key)]
        return self.env['data.update.process'].search(domain)

    def open_correction_ids(self):
        self.ensure_one()
        return self.data_corrections().open_line_ids()


    @api.onchange('correction_type')
    def on_change_correction_type_impact(self):
        self.object_id = False
        self.sql_query = False
        self.apply_type = 'some'
        self.action_type = 'update'

    @api.onchange('action_type')
    def on_change_action_type(self):
        self.field_id = False
        self.field_to_correct = False

    @api.onchange('attr_type')
    def on_change_attr_type(self):
        self.field_id = False
        self.function_id = False

    @api.onchange('function_id')
    def on_change_function_id(self):
        self.function_field_id = False

    @api.onchange('object_id', 'correction_type')
    def on_change_object_id(self):
        domain_field_id = [('id', 'in', [])]
        domain_function_field_id = [('id', 'in', [])]
        domain_all_field_id = [('id', 'in', [])]
        domain_linked_field_id = [('id', 'in', [])]
        self.field_id = False
        self.function_field_id = False
        self.order_by_field_id = False
        self.fields_to_show = False
        self.linked_field_id = False
        self.linked_field_data = False
        self.correction_rule_ids = False
        self.correction_line_ids = False
        self.correction_insert_element_ids = False
        if self.object_id:
            self.order_by_field = self.env['ir.model.fields'].search([('model', '=', self.object_id.model),
                                                                      ('name', '=', 'id')])[0].id
            domain_all_field_id = [('model', '=', self.object_id.model), ('ttype', 'not in', ('one2many',))]
            domain_field_id = list(domain_all_field_id)
            domain_function_field_id = list(domain_all_field_id)
            domain_field_id.append(('name', 'not in', MAGIC_FIELDS))
            if self.correction_type in ('insert_link', 'delete_link') and self.object_id:
                domain_linked_field_id = [('model', '=', self.object_id.model), ('ttype', '=', 'many2many')]
        return {'domain': {'field_id': domain_field_id,
                           'function_field_id': domain_function_field_id,
                           'fields_to_show': domain_field_id,
                           'order_by_field_id': domain_all_field_id,
                           'domain_linked_field_id': domain_linked_field_id}}

    @api.onchange('field_id', 'order_by_field_id')
    def on_change_field_id(self):
        domain_field_id = [('id', 'in', [])]
        domain_all_field_id = [('id', 'in', [])]
        self._init_column_data_to_set()
        if self.object_id:
            domain_all_field_id = [('model', '=', self.object_id.model), ('ttype', 'not in', ('one2many',))]
            domain_field_id = list(domain_all_field_id)
            domain_field_id.append(('name', 'not in', MAGIC_FIELDS))
        return {'domain': {'field_id': domain_field_id, 'fields_to_show': domain_field_id,
                           'order_by_field_id': domain_all_field_id}}

    @api.onchange('fields_to_show')
    def on_change_fields_to_show(self):
        domain_field_id = [('id', 'in', [])]
        domain_all_field_id = [('id', 'in', [])]
        if self.object_id:
            domain_all_field_id = [('model', '=', self.object_id.model), ('ttype', 'not in', ('one2many',))]
            domain_field_id = list(domain_all_field_id)
            domain_field_id.append(('name', 'not in', MAGIC_FIELDS))
        return {'domain': {'field_id': domain_field_id, 'fields_to_show': domain_field_id,
                           'order_by_field_id': domain_all_field_id}}

    @api.onchange('data')
    def on_change_data(self):
        self.column_data_to_replace = False
        self.column_data_to_put = False
        # self.correction_line_ids.unlink()

    @api.onchange('check_all')
    def on_change_check_all(self):
        for line in self.correction_line_ids:
            line.check = self.check_all

    @api.onchange('correction_type', 'object_to_correct', 'sql_query_type')
    def on_change_correction_type(self):
        if self.correction_type == 'sql' and self.object_to_correct and self.sql_query_type == 'insert':
            insert_elements = []
            try:
                for field_name, field_object in self.env[self.object_to_correct]._fields.items():
                    if field_object.store and field_object.name not in MAGIC_FIELDS:
                        insert_elements.append({
                            'field_name': field_object.name,
                            'is_required': field_object.required,
                            'field_type': field_object.type,
                            'field_data': field_object.name == 'company_id' and str(
                                self.env.user.company_id.id) or False,
                        })
            except KeyError as ke:
                raise UserError(_("Can not find %s in registry!you have to check the current database."))
            else:
                self.correction_insert_element_ids = insert_elements

    def _init_column_data_to_set(self):
        for field_key, field_value in self._fields.items():
            if field_key.startswith('column_data_to_set_'):
                setattr(self, field_key, False)

    @api.depends('field_id')
    def _get_is_computed_field(self):
        if not isinstance(self.id, models.NewId) and self.field_id:
            try:
                field = self.env[self.field_id.model]._fields.get(self.field_id.name)
            except KeyError as ke:
                raise UserError(_('Object or field not found in registry'))
            else:
                if field.compute and not field.related:
                    self.is_computed_field = True
                    self.compute_method = field.compute
                else:
                    field.is_computed_field = False
                    self.compute_method = False

    @api.depends('object_id')
    def _get_object_to_correct(self):
        self.object_to_correct = self.object_id and self.object_id.model or False

    @api.depends('attr_type', 'field_id','data_as_variable','function_field_id')
    def _get_field_to_correct(self):
        if self.attr_type == 'field':
            self.field_to_correct = self.field_id and self.field_id.name or False
            if not self.data_as_variable:
                self.field_to_correct_type = self.field_id and self.field_id.ttype or False
            else:
                self.field_to_correct_type = 'variable'
        elif self.attr_type == 'function':
            self.field_to_correct = self.function_field_id and self.function_field_id.name or False
            self.field_to_correct_type = self.function_field_id and self.function_field_id.ttype or False

    @api.depends('order_by_field_id')
    def _get_order_by_field(self):
        self.order_by_field = self.order_by_field_id and self.order_by_field_id.name or False

    @api.depends('correction_line_ids_count', 'correction_line_ids.check')
    def _get_total_selected_lines(self):
        for data_correction in self:
            select_count = len([selected_line for selected_line in data_correction.correction_line_ids
                                if selected_line.check])
            data_correction.total_selected_lines = "{} selected/{}".format(select_count,
                                                                           data_correction.correction_line_ids_count)

    @api.depends('correction_line_ids')
    def _get_correction_line_ids_count(self):
        for data_correction in self:
            data_correction.correction_line_ids_count = len(data_correction.correction_line_ids)

    @api.depends('correction_insert_element_ids')
    def _get_correction_insert_element_ids_count(self):
        for data_correction in self:
            data_correction.correction_insert_element_ids_count = len(
                data_correction.correction_insert_element_ids)

    def _select_correction_lines(self):
        """ Select the data to correct according to specific rules in correction_rule_ids"""
        self.ensure_one()
        if not self.user_has_groups('data_correction.group_data_correction_manager'):
            raise UserError(_("You are not authorised to do this action!"))
        this = self[0]
        cr = this.env.cr
        sql_query = this._build_sql_query("SELECT")
        lines = []
        params = ()
        try:
            cr.execute(sql_query, params)
        except psycopg2.errors.UndefinedColumn as e:
            field_to_correct = self.env[self.object_to_correct]._fields.get(self.field_to_correct,False)
            if not field_to_correct.store:
                raise ValidationError(_("The field %s is not stored field in database,can not update non stored field")%field_to_correct.name)
            else:
                raise ValidationError(e)
        except Exception as e:
                raise ValidationError(e)
        list_found = cr.dictfetchall()
        data_as_variable = False
        if this.action_type == 'update':
            if self.attr_type == 'field':
                data_to_put,data_as_variable = this._get_data_to_put()
            else:
                data_to_put = "/"
        else:
            data_to_put = False
        for row in list_found:
            line = {
                'action_type': self.action_type,
                'attr_type': self.attr_type,
                'id_found': row.get('id'),
                'field_data_found': row.get(self.field_to_correct),
                'data_to_replace': row.get(self.field_to_correct),
                'data_to_put': self._parse_data(data_to_put,data_as_variable,row.get('id')),
                'check': True,
            }
            lines.append(line)
        return lines

    def _get_data_to_put(self):
        """ Get the data to put in lines to replace existing ones,this is the default value of correction,
        we have to get it dynamically because we have many types of data : many2one,float date,datetime...
        """
        self.ensure_one()
        variable_data = False
        if not self.user_has_groups('data_correction.group_data_correction_manager'):
            raise UserError(_("You are not authorised to do this action!"))
        try:
            data_to_put_attribute = TYPE_ATTRBUTE_MAPPING[self.field_to_correct_type]
            data_to_put = getattr(self, data_to_put_attribute)
            if data_to_put_attribute == VARIABLE_COL:
                variable_data = True
        except KeyError as ke:
            raise UserError(
                _('Attribute to get data from not found in correction for type <{}>!'.format(
                    self.field_to_correct_type)))
        else:
            return (data_to_put,variable_data)

    def _parse_data(self,data_to_put,data_as_variable,record_id):
        if not data_as_variable:
            return data_to_put
        record = self.env[self.object_to_correct].browse(record_id)
        data_to_put_list = data_to_put.split(".")
        while data_to_put_list:
            try:
                field = data_to_put_list.pop(0)
                record = getattr(record,field)
            except AttributeError as ae:
                raise UserError(ae)
        data_to_put = record
        return data_to_put


    def _build_sql_query(self, type):
        """ Build the appropriate SQL query according to the fields of this
            :param type: the type of SQL query to return
            :return SQL query """
        if not self.user_has_groups('data_correction.group_data_correction_manager'):
            raise UserError(_("You are not authorised to do this action!"))
        try:
            database_table = self.env[self.object_id.model]._table
        except KeyError as ke:
            raise UserError(_("Object {} not detected in registry ".format(self.object_id.name)))
        else:
            where_clause_table = []
            if self.apply_type == "some":
                for rule_line in self.correction_rule_ids:
                    where_clause_table.append(rule_line._rule_to_sql())
            sql_query = "FROM {}".format(database_table)
            if where_clause_table:
                sql_query_where = " ".join([elem for elem in where_clause_table])
                sql_query = "{} WHERE {}".format(sql_query, sql_query_where)
            if type == "SELECT":
                sql_query = "SELECT id,{} {} ORDER BY {} {};".format(self.field_to_correct, sql_query,
                                                                     self.order_by_field, self.order_by_orientation)
            else:
                raise UserError(_("Building not implemented for type {}".format(type)))
            return sql_query

    def _import_from_file(self):
        """ Import the data to correct from file according to specific pattern """
        if not self.user_has_groups('data_correction.group_data_correction_manager'):
            raise UserError(_("You are not authorised to do this action!"))
        this = self[0]
        with TemporaryFile('wb+') as buf:
            try:
                buf.write(base64.decodestring(this.data))
                # now we determine the file format
                buf.seek(0)
                fileformat = os.path.splitext(this.filename)[-1][1:].lower()
                if fileformat == '' or fileformat not in FILE_FORMATS:
                    raise UserError(_("File format must be {}".format(" or ".join([format in FILE_FORMATS]))))
                data_table = _get_references_from_xls(buf, this.column_data_to_replace, this.column_data_to_put)
                database_data_lines = this._get_data_from_database(this.object_to_correct,
                                                                   this.field_to_correct,
                                                                   data_table,
                                                                   this.order_by_column,
                                                                   this.order_by_position,
                                                                   search_by_id=True)
            except Exception as e:
                _logger.exception(e)
                raise UserError(_(e))
            else:
                return database_data_lines

    def _get_data_from_database(self, object_to_correct, field_to_correct, data_table, order_by_column,
                                order_by_position, search_by_id=False):
        """ Get the data to correct from database
            :param object_to_correct to get the table
            :param data_table contain the data of WHERE clause in sql query
            :param order_by_position to define the default check
            :param order_by_column to get the data of ORDER BY sql query """
        self.ensure_one()
        if not self.user_has_groups('data_correction.group_data_correction_manager'):
            raise UserError(_("You are not authorised to do this action!"))
        cr = self.env.cr
        lines = []
        try:
            database_table = self.env[object_to_correct]._table
            for data_from, data_to in data_table:
                params = ()
                if not search_by_id:
                    params += (data_from,)
                    sql_query = "SELECT id,{} FROM {} WHERE {}=%s ORDER BY id;".format(field_to_correct,
                                                                                       database_table,
                                                                                       field_to_correct)
                else:
                    if isinstance(data_from, str) and '__export__' in data_from:
                        data_from_table = data_from.split("_")
                        data_from = int(data_from_table[len(data_from_table) - 1])
                    params += (data_from,)
                    sql_query = "SELECT id,id FROM {} WHERE id=%s ORDER BY id;".format(database_table)
                cr.execute(sql_query, params)
                first = True  # for get the first element
                cpt = 1  # for get the last element
                list_found = cr.dictfetchall()
                for row in list_found:
                    line = {
                        'id_found': row.get('id'),
                        'field_data_found': row.get(field_to_correct),
                        'data_to_replace': data_from,
                        'data_to_put': data_to
                    }
                    if row.get(field_to_correct) != data_to:
                        if order_by_position == 'first' and first:
                            line.update({'check': True})
                        elif order_by_position == 'last' and cpt == len(list_found):
                            line.update({'check': True})
                        elif order_by_position == 'all':
                            line.update({'check': True})
                    lines.append(line)
                    first = False
                    cpt += 1
        except KeyError as ke:
            raise UserWarning(_("Object {} not detected in registry".format(object_to_correct)))
        else:
            return lines

    def import_correction_file(self):
        """ Import the data from file and update the lines according to it"""
        this = self[0]
        if not this.user_has_groups('data_correction.group_data_correction_manager'):
            raise UserError(_("You are not authorised to do this action!"))
        correction_line_ids = this._import_from_file()
        this.correction_line_ids.unlink()
        for line in correction_line_ids:
            line.update({'correction_id': this.id})
            self.env['data.correction.line'].create(line)
        return {
            'context': self.env.context,
            'view_type': 'form',
            'view_mode': 'form',
            'res_model': self._name,
            'res_id': this.id,
            'view_id': False,
            'type': 'ir.actions.act_window',
            'target': 'new',
        }

    def select_correction_lines(self):
        """ select the lines to correct from database according to correction_rule_ids"""
        this = self[0]
        if not this.user_has_groups('data_correction.group_data_correction_manager'):
            raise UserError(_("You are not authorised to do this action!"))
        if this.apply_type == 'some':
            this._check_apply_type()
        correction_line_ids = this._select_correction_lines()
        self.check_all = True
        this.correction_line_ids.unlink()
        for line in correction_line_ids:
            line.update({'correction_id': this.id})
            self.env['data.correction.line'].create(line)

    def _check_apply_type(self):
        """ Check the apply type coherence before build SQL query
        :return True if all is good ,this return is not very important!"""
        self.ensure_one()
        if not self.user_has_groups('data_correction.group_data_correction_manager'):
            raise UserError(_("You are not authorised to do this action!"))
        if self.apply_type == 'some' and not self.correction_rule_ids:
            raise UserError(_('At least one rule must be added in this apply type!'))
        # the logic lines must be less than statement lines in count len(logic_lines) = len(statement_lines)-1
        logic_lines = len([line for line in self.correction_rule_ids if line.rule_type == 'operator'])
        statement_lines = len([line for line in self.correction_rule_ids if line.rule_type == 'statement'])
        if logic_lines != statement_lines - 1:
            raise UserError(
                _("Logic operators lines must be less then statement lines by : <logic_count = statement_count -1>"))
        # now we have to check the order logic :
        # first : operator line must be between 2 statement lines
        order_lines_checker = [line.rule_type for line in self.correction_rule_ids]
        # we use the cpt in the loop to can access the list by indexes
        cpt = 0
        for line in order_lines_checker:
            if cpt == 0 and line == 'operator':
                raise UserError(_('Operator line must be between two statement lines!'))
            try:
                if line == 'operator' and (
                        order_lines_checker[cpt - 1] != 'statement' or order_lines_checker[cpt + 1] != 'statement'):
                    raise UserError(_('Operator line must be between two statement lines!'))
            except IndexError as ie:
                raise UserError(_('Operator line must be between two statement lines!'))
            else:
                cpt += 1
        # second : 2 statement lines can not follow each other
        cpt = 0
        # we use the cpt in the loop to can access the list by indexes
        for line in order_lines_checker:
            try:
                if line == 'statement' and order_lines_checker[cpt + 1] == 'statement':
                    raise UserError(_('Two statement lines can not follow each other!'))
            except IndexError as ie:
                # the last line must be statement ,so we wait to have IndexError in the good case
                continue
        return True

    """@api.model
    def _parse_data(self, data, record):
        if not _is_variable(data):
            return data
        values = []
        origin_data = data
        for field_name_var in _get_variables(origin_data):
            try:
                value = getattr(record, field_name_var)
            except AttributeError as ae:
                raise UserError(_('Field name %s does npt exist in model %s') % (field_name_var, record._name))
            else:
                values.append(value)
                data = data.replace("{%s}" % field_name_var, "%s")
        data = data % tuple(values)
        return data
    """

    def apply_correction(self):
        """ Apply the correction to database
            :param compute_method : to set the data manually or recompute with compute_method
            :param sql_query : to directly execute insert or delete sql query
        """
        compute_method, sql_query, params, link_fields = False, False, False, False
        self.ensure_one()
        if not self.user_has_groups('data_correction.group_data_correction_manager'):
            raise UserError(_("You are not authorised to do this action!"))
        if not sql_query and self.apply_on == 'record':
            select_count = len([selected_line for selected_line in self.correction_line_ids
                                if selected_line.check])
            if select_count <= 0:
                raise UserError(_("You have to check at least one line to correct!"))
        try:
            database_table = self._detect_database_table()
        except KeyError as ke:
            raise UserWarning(_("Object {} not detected in registry!".format(self.object_to_correct)))
        else:
            cpt = 0  # to get the number of corrected data
            cr = self.env.cr  # the cursor to use in the correction
            res_database = False
            # here we have to check if we have to create new correction_log or to append to existing one
            if self.correction_class == 'new':
                update_process = self.env['data.update.process'].create({
                    'name': self.correction_key,
                    'description': self.correction_note,
                    'requested_by': self.contact_id.id
                })
            elif self.correction_class == 'append':
                update_process = self.env['data.update.process'].browse(self.env.context.get('active_id'))
            registry_object = self.env[self.object_to_correct]
            if not sql_query:
                log_text = ""
                # here is the case of update
                # if we have to delete records
                if self.apply_on == 'record' and self.action_type == 'delete':
                    # TODO : in the case of delete we have not to do this for loop!
                    for line in self.correction_line_ids:
                        if line.check:
                            # here we have to based on the compute_method params to decide wether we have to take in account
                            # the data_to_put set manually or calculate with compute method
                            # we have to get the instance modified
                            record = registry_object.browse(int(line.id_found))
                            if compute_method:
                                # here we have to call the compute_method
                                getattr(record, compute_method)()
                                line.data_to_put = getattr(record, self.field_to_correct)
                            params = ()
                            # in the case of delete we have to store backup for data
                            data_to_put = self._convert_data_to_put(line.data_to_put)
                            data = {
                                'parent_id': update_process.id,
                                'object_to_correct': self.object_to_correct,
                                'field_to_correct': self.field_to_correct,
                                'res_id': line.id_found,
                                'original_data': line.field_data_found or 'NULL',
                                'new_data': data_to_put,
                                'compute_method': compute_method and compute_method or False
                            }
                            # we have to store the record before delete
                            backup_obj = self.env['atom.correction.line.log.backup']
                            log_backup_data = backup_obj._create_backup_dictionary(database_table, line.id_found)
                            params += (line.id_found,)
                            # we have now to delete the record
                            sql_query = "DELETE FROM {} WHERE id=%s;".format(database_table)
                            cr.execute(sql_query, params)
                            self._update_correction_log(data, log_backup_data)
                            cpt += 1
                # if we have to correct fields in record(s)
                elif self.apply_on == 'record' and self.attr_type == 'field':
                    for line in self.correction_line_ids:
                        if line.check:
                            # here we have to based on the compute_method params to decide wether we have to take in account
                            # the data_to_put set manually or calculate with compute method
                            # we have to get the instance modified
                            record = registry_object.browse(int(line.id_found))
                            if compute_method:
                                # here we have to call the compute_method
                                getattr(record, compute_method)()
                                line.data_to_put = getattr(record, self.field_to_correct)
                            params = ()
                            # in the case of delete we have to store backup for data
                            log_backup_data = {}
                            data_to_put = self._convert_data_to_put(line.data_to_put)
                            data = {
                                'parent_id': update_process.id,
                                'name':update_process.name,
                                'model': self.object_to_correct,
                                'field': self.field_to_correct,
                                'res_id': int(line.id_found),
                                'ttype':self.env[self.object_to_correct]._fields[self.field_to_correct].type,
                                'original_data': line.field_data_found or 'NULL',
                                'new_data': data_to_put,
                            }
                            # we have 2 action_type : update or delete
                            if self.action_type == 'update':
                                # here we can think about the case of function update,we just use the update request here for backward compatibility
                                if res_database or data_to_put == "NULL":
                                    params += (self.env.user.id, line.id_found)
                                    sql_query = "UPDATE {} SET {}=NULL,write_uid=%s,write_date=(now() at time zone 'UTC') WHERE id=%s;".format(
                                        database_table, self.field_to_correct)
                                else:
                                    params += (line.data_to_put, self.env.user.id, line.id_found)
                                    sql_query = "UPDATE {} SET {}=%s,write_uid=%s,write_date=(now() at time zone 'UTC') WHERE id=%s;".format(
                                        database_table, self.field_to_correct)
                                cr.execute(sql_query, params)
                                # we have to recompute dependant computed fields ,
                                # this can be made by the notification of the modified field
                                if not self.prevent_trigger_computed_fields:
                                    record.modified([self.field_to_correct])
                                    for field_key, field_value in record._fields.items():
                                        if field_value.type == 'many2one':
                                            parent_record = getattr(record, field_key)
                                            if parent_record:
                                                # we have to detect the relationel field
                                                try:
                                                    parent_field_to_unvalidate = next(parent_field_key
                                                                                  for
                                                                                  parent_field_key, parent_field_value
                                                                                  in parent_record._fields.items()
                                                                                  if
                                                                                  parent_field_value.type == 'one2many' \
                                                                                  and parent_field_value.comodel_name == record._name \
                                                                                  and parent_field_value.inverse_name == field_key)
                                                except StopIteration as si:
                                                    continue
                                                else:
                                                    parent_record.modified([parent_field_to_unvalidate])
                                # we have to save all the correction events in log
                                #data.update({'field_to_correct': self.field_to_correct,
                                #             'original_data': line.field_data_found or 'NULL',
                                #             'new_data': data_to_put,
                                #             'compute_method': compute_method and compute_method or False})
                            # elif self.action_type == 'delete':
                            # we have to store the record before delete
                            #    backup_obj = self.env['atom.correction.line.log.backup']
                            #    log_backup_data = backup_obj._create_backup_dictionary(database_table,line.id_found)
                            #    params += (line.id_found,)
                            # we have now to delete the record
                            #    sql_query = "DELETE FROM {} WHERE id=%s;".format(database_table)
                            #    cr.execute(sql_query, params)
                            else:
                                raise UserError(_("Failed to detect the action type of the correction!"))
                            self._update_correction_log(data, log_backup_data)
                            cpt += 1
                # here we have to correct record(s) using dedicated function
                elif self.apply_on == 'record' and self.attr_type == 'function':
                    record_ids = [int(line.id_found) for line in self.correction_line_ids if line.check]
                    records = registry_object.browse(record_ids)
                    try:
                        # FIXME : performance issue ,find best method
                        datas = getattr(records, self.function_id)()
                    except AttributeError as ae:
                        raise UserWarning(ae)
                        # raise UserError(_('Method {} not found in {}'.format(self.function_id, record._name)))
                    except Exception as exc:
                        raise UserWarning(exc)
                    else:
                        log_backup_data = {}
                        # FIXME : performance issue ,find best method
                        for data in datas:
                            data.update({'parent_id': update_process.id})
                        self._update_correction_log(datas, log_backup_data)
                        cpt += len(datas)
                # here we have to correct constraint
                elif self.apply_on == 'constraint':
                    data = {
                        'parent_id': update_process.id,
                        'object_to_correct': self.object_to_correct,
                        'constraint_to_correct': self.constraint_name,
                        'correction_type': self.action_type
                    }
                    if self.action_type == 'update':
                        raise UserError(_("Action type <update> not implemented in constraint!"))
                    elif self.action_type == 'delete':
                        backup_obj = self.env['atom.correction.line.log.backup']
                        log_backup_data = backup_obj._create_backup_dictionary(database_table, False,
                                                                               apply_on=self.apply_on,
                                                                               cons_name=self.constraint_name,
                                                                               registry_object=registry_object)
                        constraint_name = self.env['atom.correction.line.log']._get_constraint_name(
                            database_table=database_table,
                            constraint_name=self.constraint_name)
                        self._drop_constraint(database_table, constraint_name)
                    self._update_correction_log(data, log_backup_data)
                    cpt += 1
            else:
                # here is the case of sql_query : insert or delete record
                sql_query = sql_query.format(database_table)
                self.env.cr.execute(sql_query, params)
                # we have to get the created ID
                if self.sql_query_type == 'insert':
                    new_created_id = self.env.cr.dictfetchone()
                # we have to save all the correction events in log
                data = {
                    'parent_id': update_process.id
                }
                if self.sql_query_type not in ('insert_link', 'delete_link'):
                    data.update({
                        'object_to_correct': self.object_to_correct,
                        'field_to_correct': '/',
                        'res_id': new_created_id['id'],
                        'original_data': '/',
                        'new_data': new_created_id['id'],
                    })
                    if self.sql_query_type == 'delete_link':
                        # we have to store the record before delete
                        backup_obj = self.env['atom.correction.line.log.backup']
                        # FIXME : here we must to continue coding
                else:
                    data.update({
                        'object_to_correct': database_table,
                        'column_1': link_fields.keys()[0],
                        'column_1_data': link_fields.values()[0],
                        'column_2': link_fields.keys()[1],
                        'column_2_data': link_fields.values()[1],

                    })
                data.update({'correction_type': self.sql_query_type,
                             'compute_method': compute_method and compute_method or False, })
                self._update_correction_log(data)
                cpt += 1
            return self.action_done()
            #return self._return_info_message(cpt)

    def _convert_data_to_put(self, data_to_put):
        self.ensure_one()
        if self.attr_type == 'field' and self.action_type == 'update' \
                and self.env[self.object_to_correct]._fields[self.field_to_correct].type == 'many2one' \
                and data_to_put is False:
            return "NULL"
        return data_to_put

    def _detect_database_table(self):
        """ Detect the table that must be impacted """
        try:
            if self.sql_query_type not in ('insert_link', 'delete_link'):
                database_table = self.env[self.object_to_correct]._table
            else:
                if not self.linked_field_id:
                    raise UserError(_("Linked field must be specified in the case of <Insert link>."))
                try:
                    field_object = self.env[self.object_to_correct]._fields[self.linked_field_id.name]
                except KeyError as ke:
                    raise UserError(_(
                        "Failed to find field %s in object %s." % (self.linked_field_id.name, self.object_to_correct)))
                else:
                    database_table = field_object.relation
        except KeyError as ke:
            raise UserWarning(_("Object {} not detected in registry!".format(self.object_to_correct)))
        return database_table

    def apply_recalculate_method(self):
        """ apply compute field method to recalculate the value after eventual edit of depends field value"""
        self.ensure_one()
        if not self.user_has_groups('data_correction.group_data_correction_manager'):
            raise UserError(_("You are not authorised to do this action!"))
        return self.apply_correction(self.env.context, compute_method=self.compute_method)

    def _update_correction_log(self, datas, log_backup_data=False):
        """ Update the event log table with new added lines"""
        self.ensure_one()
        if not isinstance(datas, list):
            datas = [datas]
        for data in datas:
            log_line = self.env['data.update.log'].create({
                'parent_id': data.get('parent_id'),
                'name': data.get('name'),
                'model': data.get('model'),
                'res_id': data.get('res_id'),
                'field': data.get('field'),
                'ttype': data.get('ttype'),
                'original_data': data.get('original_data'),
                'new_data': data.get('new_data'),
            })
            if log_backup_data:
                log_backup_data.update({'correction_log_line_id': log_line.id})
                self.env['atom.correction.line.log.backup'].create(log_backup_data)

    def _return_info_message(self, cpt):
        """
         Display the information window
         :param cpt the number of updated lines in database
         :return the information window with the number of update lines
         """
        message = "<strong>{}</strong> record(s) is successfully processed.".format(cpt)
        info_message = self.env['info.message'].create({
            'message': message
        })
        return {
            'context': self.env.context,
            'view_type': 'form',
            'view_mode': 'form',
            'res_model': 'info.message',
            'res_id': info_message.id,
            'view_id': False,
            'type': 'ir.actions.act_window',
            'target': 'new',
        }

    def sql_query_elements_data(self):
        """ Insert data or link (Many2many)"""
        self.ensure_one()
        self._check_sql_query_elements()
        if self.sql_query_type not in ('insert', 'insert_link', 'delete_link'):
            raise UserError(_('SQL query type must be <Insert>'))
        fields = self._get_fields_to_manipulate()
        try:
            sql_query = SQL_QUERY_TYPE_QUERY_MAP[self.sql_query_type]
        except KeyError as ke:
            raise UserError(_('Failed to find SQL Query <{}> sql query type!'.format(self.sql_query_type)))
        if self.sql_query_type == 'insert':
            sql_query_fields_names = "({},create_date,write_date)".format(
                ",".join(field_name for field_name in fields.keys()))
            sql_query_fields_values = "({},(now() at time zone 'UTC'),(now() at time zone 'UTC'))".format(
                ",".join('%s' for __ in fields.values()))
        elif self.sql_query_type in ('insert_link',):
            sql_query_fields_names = "({})".format(",".join(field_name for field_name in fields.keys()))
            sql_query_fields_values = "({})".format(",".join('%s' for __ in fields.values()))
        elif self.sql_query_type in ('delete_link',):
            first_key = fields.keys()[0]
            sql_query_where = " WHERE {}=%s".format(first_key)
            for key in fields.keys():
                if key == first_key:
                    continue
                sql_query_where += " AND {}=%s".format(key)
        if self.sql_query_type not in ('delete_link',):
            sql_query_value = " VALUES "
            if self.sql_query_type == 'insert':
                sql_query_returning = " RETURNING id;"
            else:
                sql_query_returning = " ;"
            sql_query += sql_query_fields_names + sql_query_value + sql_query_fields_values + sql_query_returning
        else:
            sql_query += sql_query_where
        params = tuple([field_value for field_value in fields.values()])
        link_fields = self.sql_query_type in ('insert_link', 'delete_link') and fields or False
        return self.apply_correction(self.env.context, compute_method=False, sql_query=sql_query, params=params,
                                     link_fields=link_fields)

    def _get_fields_to_manipulate(self):
        """ Get the field from the view in all cases"""
        self.ensure_one()
        fields = {}
        if self.sql_query_type == 'insert':
            fields.update({'create_uid': self.env.user.id,
                           'write_uid': self.env.user.id})
            if self.ignore_empty_fields:
                for field in self.correction_insert_element_ids:
                    if field.field_data:
                        fields.update({field.field_name: self._convert_field_data(field)})
            else:
                for field in self.correction_insert_element_ids:
                    fields.update({field.field_name: self._convert_field_data(field)})
            if not fields:
                raise UserError(_("At least one field must be specified in order to insert record!"))
        elif self.sql_query_type in ('insert_link', 'delete_link'):
            try:
                field_object = self.env[self.object_to_correct]._fields[self.linked_field_id.name]
            except KeyError as ke:
                raise UserError(
                    _("Failed to find field %s in object %s." % (self.linked_field_id.name, self.object_to_correct)))
            else:
                fields.update({self.linked_field_id.column1: self.linked_field_data_source})
                fields.update({self.linked_field_id.column2: self.linked_field_data})
        return fields

    @api.model
    def _convert_field_data(self, field):
        """ Convert the specified data of field according to the field type"""
        try:
            converted_data = TYPE_INSERT_TYPE_MAPPING[field.field_type](field.field_data)
        except KeyError as ke:
            raise UserError(
                _("Field type <%s> Can not be converted or method of convert not found!" % field.field_type))
        else:
            return converted_data

    def _check_sql_query_elements(self):
        self.ensure_one()
        self._check_high_security_access()
        if not self.correction_type == 'sql':
            raise UserError(_("The type of correction must be SQL in order to execute SQL request"))
        if not self.sql_query_type:
            raise UserError(_('SQL query type must be specified'))
        if not self.object_id:
            raise UserError(_('Object must be specified!'))
        if self.sql_query_type == 'insert' and not self.correction_insert_element_ids:
            raise UserError(_('Record-s data must be specified'))
        self._check_manip_link_access()

    def _check_manip_link_access(self):
        """Check the insert or delete link access"""
        self.ensure_one()
        if not self.object_id:
            raise UserError(_('Object must be specified!'))
        if self.sql_query_type in ('insert_link', 'delete_link'):
            if not self.sql_query_type:
                raise UserError(_('SQL query type must be specified'))
            if not self.linked_field_data_source:
                raise UserError(_('ID affected must be specified!'))
            if not self.linked_field_id:
                raise UserError(_('Linked field must be specified'))
            if not self.linked_field_data:
                raise UserError(_('ID to link must be specified'))

    def _check_high_security_access(self):
        """ Check high security access"""
        if not self.user_has_groups('data_correction.group_data_correction_manager'):
            raise UserError(_("You are not authorised to do this action!"))


    def action_done(self):
        self._action_reset_variable_fields()
        return self._action_done()

    def _action_reset_variable_fields(self):
        self.action_type = 'update'
        self.sql_query = False
        self.sql_query_type = False
        self.object_id = False
        #self.field_id = False
        self.function_id = False
        self.attr_type = 'field'
        #self.order_by_field_id = False
        self.order_by_orientation = 'ASC'
        self.correction_insert_element_ids.unlink()
        self.correction_rule_ids.unlink()
        self.correction_line_ids.unlink()
        #self._init_column_data_to_set()

    def _action_done(self):
        self.write({'state':'done'})

    def action_cancel_append(self):
        self.ensure_one()
        if not self.user_has_groups('data_correction.group_data_correction_manager'):
            raise UserError(_("You are not authorised to do this action!"))
        self._action_reset_variable_fields()
        self._action_done()

    def action_append_correction(self):
        """ Append new correction to already existing correction log """
        self.ensure_one()
        if not self.user_has_groups('data_correction.group_data_correction_manager'):
            raise UserError(_("You are not authorised to do this action!"))
        self._action_append()
        current_action = self.env.ref('data_correction.data_correction_action')
        new_context = ast.literal_eval(current_action.context)
        new_context.update({'form_view_initial_mode':'edit'})
        return {
            'name': current_action.name,
            'view_mode': current_action.view_mode,
            'views': [(self.env.ref('data_correction.data_correction_view_form').id, 'form')],
            'res_model': current_action.res_model,
            'type': 'ir.actions.act_window',
            'target': 'current',
            'res_id':self.id,
            'context':new_context,

        }




    def _action_append(self):
        self.write({'appended':True,'state':'draft'})




class DataCorrectionLine(models.TransientModel):
    _name = "data.correction.line"

    correction_id = fields.Many2one('data.correction', ondelete='cascade')
    action_type = fields.Selection(ACTION_TYPE_SELECT)
    attr_type = fields.Selection(ATTR_TYPE_SELECT)
    check = fields.Boolean(string='Selected', default=False)
    id_found = fields.Char(string='ID of data to replace')
    field_data_found = fields.Char('Data found')
    data_to_replace = fields.Char(string='Data to replace')
    data_to_put = fields.Char(string='Data to put')
    data_as_variable = fields.Boolean(string="Data as variable", default=False,
                                      help="Check this if you want that data to set to be processed as variable not fixed value")


class DataCorrectionRule(models.TransientModel):
    _name = 'data.correction.rule'

    # fields
    correction_id = fields.Many2one('data.correction', ondelete='cascade')
    rule_type = fields.Selection(RULE_TYPE, string='Rule type')
    logic_operator = fields.Selection(RULE_LOGIN_OPERATION, string='Logic Operator')
    statement_field_id = fields.Many2one('ir.model.fields', string='Field')
    statement_operation = fields.Selection(RULE_OPERATIONS, string='Operator')
    statement_value = fields.Char(string='Value')

    @api.onchange('rule_type', 'correction_id.object_id', 'statement_field_id')
    def on_change_rule_type(self):
        self.statement_operation = False
        self.statement_value = False
        domain_statement_field_id = [('id', 'in', [])]
        if self.rule_type == 'statement' and self.correction_id.object_id:
            model = self.correction_id.object_id.model
            domain_statement_field_id = [('model', '=', model), ('ttype', 'not in', ('one2many', 'many2many'))]
        return {'domain': {'statement_field_id': domain_statement_field_id}}

    def _rule_to_sql(self):
        self.ensure_one()
        if self.rule_type == 'operator':
            return self.logic_operator
        elif self.rule_type == 'statement':
            if self.statement_operation in [elem for elem, _ in ONE_STATEMENT_OPERATIONS]:
                return "{} {}".format(self.statement_field_id.name,
                                      self.statement_operation)
            elif self.statement_operation in [elem for elem, _ in TWO_STATEMENT_OPERATIONS]:
                return "{} {} {}".format(self.statement_field_id.name,
                                         self.statement_operation,
                                         self._process_statement_value())
        else:
            raise UserError(_("Rule type {} represent not implemented case!".format(self.rule_type)))

    def _rule_to_domain(self):
        self.ensure_one()
        if self.rule_type == 'operator':
            return self.logic_operator
        elif self.rule_type == 'statement':
            if self.statement_operation in [elem for elem, _ in ONE_STATEMENT_OPERATIONS]:
                return "{} {}".format(self.statement_field_id.name,
                                      self.statement_operation)
            elif self.statement_operation in [elem for elem, _ in TWO_STATEMENT_OPERATIONS]:
                return "{} {} {}".format(self.statement_field_id.name,
                                         self.statement_operation,
                                         self._process_statement_value())
        else:
            raise UserError(_("Rule type {} represent not implemented case!".format(self.rule_type)))

    def _process_statement_value(self):
        statement_value = self.statement_value
        if self.statement_field_id.ttype in ('text', 'char', 'date', 'datetime', 'selection') \
                and not isinstance(statement_value, str):
            value = "'{}'".format(statement_value)
        else:
            value = "{}".format(statement_value)
        return value


class DataCorrectionInsertElement(models.TransientModel):
    _name = "data.correction.insert.element"

    # fields
    correction_id = fields.Many2one('data.correction', ondelete='cascade')
    field_name = fields.Char(string='Field', required=True)
    field_data = fields.Char(string='Data')
    field_type = fields.Char(string='Type')
    is_required = fields.Boolean(string='Required')
